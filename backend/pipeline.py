import asyncio
import base64
import io
import time
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional

from PIL import Image

from backend.sources.base import VideoSource
from backend.classifiers import create_classifier
from backend.match_db import MatchDB
from backend.output_manager import OutputManager
from backend.pre_filter import PreFilter
from backend.adaptive_sampler import AdaptiveSampler
from backend.consistency_checker import ConsistencyChecker
from backend.task_manager import TaskManager
from backend.utils import logger, format_time, parse_time, get_active_categories


@dataclass
class CapturedFrame:
    jpeg_bytes: bytes
    video_time: float
    video_part: int


class Pipeline:
    def __init__(
        self,
        source: VideoSource,
        match: dict,
        targets: dict,
        start_time: str,
        config: dict,
        broadcast_fn: Callable[[dict], Awaitable[None]],
        capture_id: Optional[int] = None,
        provider: str = "openai",
        task_id: str = "camera_angle",
    ):
        self.source = source
        self.match = match
        self.targets = targets
        self.start_time_str = start_time
        self.config = config
        self.broadcast = broadcast_fn
        self.capture_id = capture_id
        self.provider = provider
        self.task_id = task_id

        # Load task template
        tm = TaskManager()
        self.task = tm.get_task(task_id)
        self.categories = get_active_categories(self.task)

        # Create classifier
        classifier_config = dict(config)
        classifier_config["openai_api_key"] = config.get("openai", {}).get("api_key", "")
        classifier_config["gemini_api_key"] = config.get("gemini", {}).get("api_key", "")
        # Also pull from env if not in config
        from backend.utils import get_openai_key, get_gemini_key
        if not classifier_config["openai_api_key"]:
            classifier_config["openai_api_key"] = get_openai_key()
        if not classifier_config["gemini_api_key"]:
            classifier_config["gemini_api_key"] = get_gemini_key()
        # Pass model settings
        classifier_config["openai_model"] = config.get("openai", {}).get("model", "gpt-4o-mini")
        classifier_config["openai_detail"] = config.get("openai", {}).get("detail", "low")
        classifier_config["gemini_model"] = config.get("gemini", {}).get("model", "gemini-2.0-flash")
        classifier_config["gemini_free_tier"] = config.get("gemini", {}).get("free_tier", True)

        self.classifier = create_classifier(provider, self.task, classifier_config)
        self.output = OutputManager(match, config["output"]["base_dir"], categories=self.categories)

        # Pre-filter
        pf_config = config.get("pre_filter", {})
        self.pre_filter = PreFilter(enabled=pf_config.get("enabled", True))

        # Adaptive sampler
        sampling = config.get("sampling", {})
        self.sampler = AdaptiveSampler(
            base_interval=sampling.get("interval_seconds", 2.0),
            min_interval=sampling.get("min_interval", 1.0),
            max_interval=sampling.get("max_interval", 6.0),
            enabled=sampling.get("adaptive", True),
        )

        # Consistency checker
        self.consistency = ConsistencyChecker()

        self.interval = sampling.get("interval_seconds", 2.0)
        self.thumbnail_width = config["output"].get("thumbnail_width", 320)

        self.status = "idle"
        self._queue: asyncio.Queue[CapturedFrame] = asyncio.Queue(maxsize=20)
        self._start_wall_time = 0.0
        self._current_video_time = 0.0
        self._video_duration = 0.0
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_requested = False
        self.total_classified_local = 0

        self.saved_counts: dict[str, int] = {t: 0 for t in self.categories}

        existing = self.output.get_existing_counts()
        for cam, count in existing.items():
            self.saved_counts[cam] = count
        self._adjusted_targets = {}
        for cam in self.categories:
            original = targets.get(cam, 0)
            already = existing.get(cam, 0)
            self._adjusted_targets[cam] = max(0, original - already)
            if already > 0:
                logger.info(f"Resuming: {cam} has {already}/{original} already captured")

        # Track last classification for adaptive sampler
        self._last_classification = None
        self._last_pre_filter = None

    def _targets_met(self) -> dict[str, bool]:
        """Return {category: True/False} where True means target is met."""
        return {
            cat: self.saved_counts.get(cat, 0) >= self.targets.get(cat, 0)
            for cat in self.categories
            if self.targets.get(cat, 0) > 0
        }

    def _all_targets_met(self) -> bool:
        tm = self._targets_met()
        return bool(tm) and all(tm.values())

    async def run(self):
        self.status = "capturing"
        self._start_wall_time = time.time()

        try:
            self._video_duration = await self.source.get_duration()
            logger.info(f"Video duration: {self._video_duration:.1f}s")
            logger.info(f"Provider: {self.provider}, Task: {self.task_id}")

            start_seconds = parse_time(self.start_time_str)
            if start_seconds > 0:
                await self.source.seek_to(start_seconds)
                await asyncio.sleep(1)

            await self.broadcast({"type": "status", "status": "capturing", "message": "Capturing frames..."})

            await asyncio.gather(
                self._capture_loop(),
                self._classify_loop(),
                self._broadcast_loop(),
            )

        except Exception as e:
            logger.exception(f"Pipeline error: {e}")
            await self.broadcast({"type": "error", "message": str(e)})
            self.status = "error"
            if self.capture_id:
                try:
                    db = MatchDB()
                    db.fail_capture(self.capture_id, str(e))
                    db.close()
                except Exception:
                    pass
        finally:
            self.output.generate_metadata_csv()
            duration = time.time() - self._start_wall_time
            self.output.generate_summary_json(self.classifier, duration)

            if self.status != "error":
                self.status = "completed"
                total_captured = sum(self.saved_counts.values())
                total_target = sum(self.targets.get(cam, 0) for cam in self.categories)

                if self.capture_id:
                    try:
                        db = MatchDB()
                        db.complete_capture(
                            self.capture_id,
                            total_captured=total_captured,
                            total_classified=self.classifier.get_call_count(),
                            api_cost=self.classifier.get_cost(),
                            output_dir=self.output.get_output_dir(),
                            duration=duration,
                        )
                        db.close()
                    except Exception:
                        pass

                await self.broadcast({
                    "type": "completed",
                    "summary": {
                        "total_captured": total_captured,
                        "total_target": total_target,
                        "duration_minutes": round(duration / 60, 1),
                        "api_cost": round(self.classifier.get_cost(), 6),
                        "provider": self.provider,
                        "output_dir": self.output.get_output_dir(),
                        "counts": {
                            cam: {
                                "target": self.targets.get(cam, 0),
                                "captured": self.saved_counts.get(cam, 0),
                            }
                            for cam in self.categories
                        },
                        "pre_filter_stats": self.pre_filter.get_stats(),
                    },
                })

            await self.source.close()

    async def _capture_loop(self):
        while self.status == "capturing":
            await self._pause_event.wait()
            if self._stop_requested or self.status != "capturing":
                break

            if await self.source.is_ended():
                found_next = await self.source.handle_next_part()
                if not found_next:
                    logger.info("Video ended, no more parts")
                    self.status = "completed"
                    break
                self._video_duration = await self.source.get_duration()
                continue

            jpeg_bytes = await self.source.capture_frame()
            if jpeg_bytes:
                video_time = await self.source.get_current_time()
                if self.source.current_part > 1:
                    video_time += self.source.part1_duration

                self._current_video_time = video_time

                # ── Pre-filter ──
                pf_result = self.pre_filter.analyze(jpeg_bytes)
                self._last_pre_filter = pf_result

                if not pf_result["pass"]:
                    await self.broadcast({
                        "type": "frame_filtered",
                        "video_time": video_time,
                        "reason": pf_result["reason"],
                    })
                else:
                    frame = CapturedFrame(
                        jpeg_bytes=jpeg_bytes,
                        video_time=video_time,
                        video_part=self.source.current_part,
                    )

                    try:
                        self._queue.put_nowait(frame)
                        if self.total_classified_local < 3:
                            logger.info(f"Captured frame at {format_time(video_time)} ({len(jpeg_bytes)} bytes), queue size: {self._queue.qsize()}")
                    except asyncio.QueueFull:
                        logger.warning("Frame queue full, dropping frame")
            else:
                logger.warning("Screenshot returned empty")

            # ── Adaptive interval ──
            interval = self.sampler.get_interval(
                last_classification=self._last_classification,
                pre_filter_result=self._last_pre_filter or {},
                targets_status=self._targets_met(),
            )
            await asyncio.sleep(interval)

    async def _classify_loop(self):
        while self.status in ("capturing", "paused") or not self._queue.empty():
            if self._stop_requested and self._queue.empty():
                break

            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                if self.status == "completed" or self._stop_requested:
                    break
                continue

            self.total_classified_local += 1
            logger.info(f"Classifying frame #{self.total_classified_local} at {format_time(frame.video_time)}...")
            classification = await self.classifier.classify_frame(frame.jpeg_bytes)
            self._last_classification = classification

            classified_as = classification.get("classified_as", "OTHER")
            conf = classification.get("confidence", 0)
            is_pending = classification.get("is_pending", False)
            logger.info(f"Frame #{self.total_classified_local}: {classified_as} (conf={conf:.2f})")

            # ── Consistency check ──
            scene_changed = (self._last_pre_filter or {}).get("scene_change", False)
            consistency = self.consistency.check(classified_as, scene_changed)
            if consistency["anomaly"]:
                logger.info(f"Anomaly: {consistency['note']}")

            # ── Save decision ──
            if is_pending:
                filepath = self.output.save_frame_to_pending(frame.jpeg_bytes, frame.video_time)
                thumbnail_b64 = self._make_thumbnail(frame.jpeg_bytes)
                await self.broadcast({
                    "type": "frame_classified",
                    "filename": filepath.name,
                    "video_time": frame.video_time,
                    "classified_as": "PENDING",
                    "confidence": 0,
                    "saved": True,
                    "thumbnail_b64": thumbnail_b64,
                    "is_pending": True,
                })
            else:
                target_for_type = self._adjusted_targets.get(classified_as, 0)
                current_saved = self.saved_counts.get(classified_as, 0)

                if target_for_type > 0 and current_saved < self.targets.get(classified_as, 0):
                    filepath = await self.output.save_frame(
                        frame.jpeg_bytes, frame.video_time, classification, frame.video_part
                    )
                    self.saved_counts[classified_as] = self.saved_counts.get(classified_as, 0) + 1
                    logger.info(f"Saved {filepath.name} ({classified_as}: {self.saved_counts[classified_as]}/{self.targets.get(classified_as, 0)})")

                    if self.capture_id:
                        try:
                            db = MatchDB()
                            db.record_frame(
                                capture_id=self.capture_id,
                                filename=filepath.name,
                                filepath=str(filepath),
                                video_time=frame.video_time,
                                video_part=frame.video_part,
                                classification=classification,
                            )
                            db.close()
                        except Exception as e:
                            logger.debug(f"DB record_frame failed: {e}")

                    thumbnail_b64 = self._make_thumbnail(frame.jpeg_bytes)

                    await self.broadcast({
                        "type": "frame_classified",
                        "filename": filepath.name,
                        "video_time": frame.video_time,
                        "classified_as": classified_as,
                        "confidence": classification["confidence"],
                        "saved": True,
                        "thumbnail_b64": thumbnail_b64,
                        "anomaly": consistency["anomaly"],
                        "suggested_type": consistency.get("suggested_type"),
                    })
                else:
                    await self.broadcast({
                        "type": "frame_skipped",
                        "video_time": frame.video_time,
                        "classified_as": classified_as,
                        "reason": "target_met",
                    })

            if self._all_targets_met():
                logger.info("All targets met!")
                self.status = "completed"
                break

    async def _broadcast_loop(self):
        while self.status in ("capturing", "paused"):
            if self._stop_requested:
                break

            total_captured = sum(self.saved_counts.values())
            total_target = sum(self.targets.get(cam, 0) for cam in self.categories)

            progress = {
                "type": "progress",
                "video_time": self._current_video_time,
                "video_duration": self._video_duration + (
                    self.source.part1_duration if self.source.current_part > 1 else 0
                ),
                "video_part": self.source.current_part,
                "total_parts": self.source.total_parts,
                "counts": {
                    cam: {
                        "target": self.targets.get(cam, 0),
                        "captured": self.saved_counts.get(cam, 0),
                    }
                    for cam in self.categories
                },
                "total_captured": total_captured,
                "total_target": total_target,
                "total_classified": self.classifier.get_call_count(),
                "api_cost": self.classifier.get_cost(),
                "provider": self.provider,
                "pre_filter_stats": self.pre_filter.get_stats(),
            }
            await self.broadcast(progress)
            await asyncio.sleep(1)

    def _make_thumbnail(self, jpeg_bytes: bytes) -> str:
        try:
            img = Image.open(io.BytesIO(jpeg_bytes))
            ratio = self.thumbnail_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((self.thumbnail_width, new_height), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=60)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception:
            return ""

    def pause(self):
        self.status = "paused"
        self._pause_event.clear()
        pause_time = format_time(self._current_video_time)
        logger.info(f"Paused at {pause_time}")
        asyncio.create_task(self.broadcast({
            "type": "status",
            "status": "paused",
            "message": f"Paused at {pause_time}",
            "pause_time": pause_time,
        }))

    def resume(self):
        self.status = "capturing"
        self._pause_event.set()
        logger.info("Resumed")
        asyncio.create_task(self.broadcast({
            "type": "status",
            "status": "capturing",
            "message": "Resumed capture",
        }))

    def stop(self):
        self._stop_requested = True
        self.status = "completed"
        self._pause_event.set()
        logger.info("Stop requested")

    def get_status(self) -> dict:
        total_captured = sum(self.saved_counts.values())
        total_target = sum(self.targets.get(cam, 0) for cam in self.categories)

        return {
            "type": "progress",
            "video_time": self._current_video_time,
            "video_duration": self._video_duration,
            "video_part": self.source.current_part,
            "total_parts": self.source.total_parts,
            "counts": {
                cam: {
                    "target": self.targets.get(cam, 0),
                    "captured": self.saved_counts.get(cam, 0),
                }
                for cam in self.categories
            },
            "total_captured": total_captured,
            "total_target": total_target,
            "total_classified": self.classifier.get_call_count(),
            "api_cost": self.classifier.get_cost(),
            "provider": self.provider,
            "pre_filter_stats": self.pre_filter.get_stats(),
        }
