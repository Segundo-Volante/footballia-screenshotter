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
        capture_mode: str = "full_match",
        goal_times: list[dict] = None,
        goal_window: int = 30,
        db: Optional["MatchDB"] = None,
    ):
        self.source = source
        self.match = match
        self.targets = targets
        self.start_time_str = start_time
        self.config = config
        self.broadcast = broadcast_fn
        self.capture_id = capture_id
        self.db = db
        self.provider = provider
        self.task_id = task_id
        self.capture_mode = capture_mode
        self.goal_times = goal_times or []
        self.goal_window = goal_window

        # Pre-compute goal capture ranges if in goals_only mode
        self._goal_ranges: list[tuple[float, float]] = []
        if capture_mode == "goals_only" and self.goal_times:
            for g in self.goal_times:
                minute = g.get("minute", 0)
                center = minute * 60.0  # Convert to seconds
                start = max(0, center - goal_window)
                end = center + goal_window
                self._goal_ranges.append((start, end))
            # Sort and merge overlapping ranges
            self._goal_ranges.sort()
            merged = [self._goal_ranges[0]]
            for start, end in self._goal_ranges[1:]:
                if start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            self._goal_ranges = merged

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
        self._current_capture_time = 0.0  # For local file seeking model
        self._video_duration = 0.0
        self._capture_duration = 0.0  # Wall-clock duration of capture
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
            self._current_capture_time = start_seconds
            if start_seconds > 0:
                await self.source.seek_to(start_seconds)
                if self.source.get_source_name() != "local_file":
                    await asyncio.sleep(1)

            await self.broadcast({"type": "status", "status": "capturing", "message": "Capturing frames..."})

            if self.capture_mode == "goals_only" and self._goal_ranges:
                await self._run_goals_only()
            else:
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
                    db = self.db or MatchDB()
                    db.fail_capture(self.capture_id, str(e))
                    if not self.db:
                        db.close()
                except Exception:
                    pass
        finally:
            self.output.generate_metadata_csv()
            duration = time.time() - self._start_wall_time
            self._capture_duration = duration
            self.output.generate_summary_json(self.classifier, duration)

            if self.status != "error":
                self.status = "completed"
                total_captured = sum(self.saved_counts.values())
                total_target = sum(self.targets.get(cam, 0) for cam in self.categories)

                if self.capture_id:
                    try:
                        db = self.db or MatchDB()
                        db.complete_capture(
                            self.capture_id,
                            total_captured=total_captured,
                            total_classified=self.classifier.get_call_count(),
                            api_cost=self.classifier.get_cost(),
                            output_dir=self.output.get_output_dir(),
                            duration=duration,
                        )
                        if not self.db:
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

                # ── Generate annotation_ready/ package ──
                try:
                    from backend.annotation_bridge import AnnotationBridge
                    import json as _json
                    from pathlib import Path

                    # Load scraped data if available
                    scraped = {}
                    if self.db and self.match.get("id"):
                        match_record = self.db.get_match(self.match["id"])
                        if match_record:
                            for field in ["home_lineup_json", "away_lineup_json", "home_coach_json",
                                          "away_coach_json", "goals_json", "result_json"]:
                                val = match_record.get(field, "")
                                if val:
                                    try:
                                        scraped[field.replace("_json", "")] = _json.loads(val)
                                    except Exception:
                                        pass
                            scraped["home_team"] = match_record.get("team_name", "")
                            scraped["away_team"] = match_record.get("opponent", "")
                            scraped["venue"] = match_record.get("venue", "")
                            scraped["stage"] = match_record.get("stage", "")
                            scraped["season"] = match_record.get("season", "")
                            scraped["competition"] = match_record.get("competition", "")

                    # Get all frame records for this capture
                    frames = []
                    if self.db and self.capture_id:
                        frames = self.db.get_capture_frames(self.capture_id)

                    if frames:
                        # Load custom bridge mapping if it exists
                        bridge_mapping = None
                        bridge_file = Path("config/annotation_bridge.json")
                        if bridge_file.exists():
                            try:
                                bridge_data = _json.loads(bridge_file.read_text(encoding="utf-8"))
                                bridge_mapping = bridge_data.get("mapping")
                            except Exception:
                                pass

                        capture_data = {
                            "provider": self.provider,
                            "task_id": self.task.get("id", "camera_angle"),
                            "capture_mode": self.capture_mode,
                            "source_type": self.source.get_source_name(),
                            "api_cost": self.classifier.get_cost(),
                            "api_calls": self.classifier.get_call_count(),
                            "duration_seconds": self._capture_duration,
                            "filter_stats": self.pre_filter.get_stats(),
                            "pre_filter_enabled": self.pre_filter.enabled,
                            "adaptive": self.sampler.enabled,
                            "interval_base": self.sampler.base_interval,
                        }

                        bridge = AnnotationBridge(
                            output_dir=self.output.get_output_dir(),
                            match_data=dict(self.match),
                            capture_data=capture_data,
                            bridge_mapping=bridge_mapping,
                        )
                        ready_path = bridge.generate(frames, scraped)

                        await self.broadcast({
                            "type": "annotation_ready",
                            "path": ready_path,
                            "frames": len(frames),
                        })

                except Exception as e:
                    logger.warning(f"annotation_ready generation failed (non-fatal): {e}")

            await self.source.close()

    async def _capture_loop(self):
        is_local = self.source.get_source_name() == "local_file"

        while self.status == "capturing":
            await self._pause_event.wait()
            if self._stop_requested or self.status != "capturing":
                break

            if is_local:
                # Local file: explicitly seek to next timestamp
                if self._current_capture_time >= await self.source.get_duration():
                    logger.info("Local file: reached end of video")
                    self.status = "completed"
                    break
                await self.source.seek_to(self._current_capture_time)
            else:
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
                if not is_local and self.source.current_part > 1:
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
                if is_local:
                    # End of file
                    logger.info("Local file: no more frames")
                    self.status = "completed"
                    break
                logger.warning("Screenshot returned empty")

            # ── Adaptive interval ──
            interval = self.sampler.get_interval(
                last_classification=self._last_classification,
                pre_filter_result=self._last_pre_filter or {},
                targets_status=self._targets_met(),
            )

            if is_local:
                # For local files, advance the seek position (no sleep needed)
                self._current_capture_time = video_time + interval
            else:
                # For web sources, sleep and let the video play
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

                    if self.capture_id and self.db:
                        try:
                            self.db.record_frame(
                                capture_id=self.capture_id,
                                filename=filepath.name,
                                filepath=str(filepath),
                                video_time=frame.video_time,
                                video_part=frame.video_part,
                                classification=classification,
                            )
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

    async def _run_goals_only(self):
        """
        Capture frames only around goal times.
        Seeks to each goal time window and captures with dense interval (1 second).
        """
        await self.broadcast({
            "type": "status",
            "status": "capturing",
            "message": f"Goals Only mode: {len(self._goal_ranges)} time windows to capture",
        })

        for i, (range_start, range_end) in enumerate(self._goal_ranges):
            if self.status != "capturing":
                break

            await self.broadcast({
                "type": "status",
                "status": "capturing",
                "message": f"Seeking to goal window {i + 1}/{len(self._goal_ranges)} ({self._format_goal_time(range_start)})",
            })

            # Seek to start of this goal window
            await self.source.seek_to(range_start)
            await self.source.start_playback()

            dense_interval = 1.0

            while self.status == "capturing":
                current_time = await self.source.get_current_time()
                self._current_video_time = current_time

                # Check if we've passed the end of this goal window
                if current_time > range_end:
                    break

                # Check if video has ended
                if await self.source.is_ended():
                    has_next = await self.source.handle_next_part()
                    if not has_next:
                        break

                # Handle pause
                if self.status == "paused":
                    await self._pause_event.wait()
                    if self.status != "capturing":
                        break

                # Standard capture flow: capture -> pre-filter -> classify -> save
                frame_bytes = await self.source.capture_frame()
                if frame_bytes is None:
                    await asyncio.sleep(dense_interval)
                    continue

                video_time = await self.source.get_current_time()

                # Pre-filter (still applies — skip black frames and duplicates)
                pf_result = self.pre_filter.analyze(frame_bytes)
                if not pf_result["pass"]:
                    await asyncio.sleep(dense_interval)
                    continue

                # Classify
                classification = await self.classifier.classify_frame(frame_bytes)
                classified_as = classification.get("classified_as", "OTHER")

                # In Goals Only mode, save ALL passing frames regardless of target
                filepath = await self.output.save_frame(frame_bytes, video_time, classification, self.source.current_part)
                self.saved_counts[classified_as] = self.saved_counts.get(classified_as, 0) + 1

                if self.capture_id and self.db:
                    try:
                        self.db.record_frame(
                            capture_id=self.capture_id,
                            filename=filepath.name,
                            filepath=str(filepath),
                            video_time=video_time,
                            video_part=self.source.current_part,
                            classification=classification,
                        )
                    except Exception:
                        pass

                thumbnail_b64 = self._make_thumbnail(frame_bytes)

                # Broadcast progress
                await self.broadcast({
                    "type": "frame_classified",
                    "filename": filepath.name,
                    "classified_as": classified_as,
                    "video_time": video_time,
                    "confidence": classification.get("confidence", 0),
                    "saved": True,
                    "thumbnail_b64": thumbnail_b64,
                    "goal_window": i + 1,
                    "total_windows": len(self._goal_ranges),
                    "counts": {
                        cam: {
                            "target": self.targets.get(cam, 0),
                            "captured": self.saved_counts.get(cam, 0),
                        }
                        for cam in self.categories
                    },
                    "api_cost": self.classifier.get_cost(),
                    "provider": self.provider,
                })

                await asyncio.sleep(dense_interval)

    def _format_goal_time(self, seconds: float) -> str:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m:02d}:{s:02d}"

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
