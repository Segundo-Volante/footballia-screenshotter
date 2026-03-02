import asyncio
import base64
import io
import time
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional

from PIL import Image

from backend.sources.base import VideoSource
from backend.camera_classifier import CameraClassifier
from backend.match_db import MatchDB
from backend.output_manager import OutputManager
from backend.utils import logger, format_time, parse_time, CAMERA_TYPES


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
    ):
        self.source = source
        self.match = match
        self.targets = targets
        self.start_time_str = start_time
        self.config = config
        self.broadcast = broadcast_fn
        self.capture_id = capture_id

        self.classifier = CameraClassifier(config["openai"], targets)
        self.output = OutputManager(match, config["output"]["base_dir"])

        self.interval = config["sampling"].get("interval_seconds", 2.0)
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

        self.saved_counts: dict[str, int] = {t: 0 for t in CAMERA_TYPES}

        existing = self.output.get_existing_counts()
        for cam, count in existing.items():
            self.saved_counts[cam] = count
        self._adjusted_targets = {}
        for cam in CAMERA_TYPES:
            original = targets.get(cam, 0)
            already = existing.get(cam, 0)
            self._adjusted_targets[cam] = max(0, original - already)
            if already > 0:
                logger.info(f"Resuming: {cam} has {already}/{original} already captured")

    async def run(self):
        self.status = "capturing"
        self._start_wall_time = time.time()

        try:
            # Source is already set up by server.py before Pipeline creation.
            self._video_duration = await self.source.get_duration()
            logger.info(f"Video duration: {self._video_duration:.1f}s")

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
                total_target = sum(self.targets.get(cam, 0) for cam in CAMERA_TYPES)

                if self.capture_id:
                    try:
                        db = MatchDB()
                        db.complete_capture(
                            self.capture_id,
                            total_captured=total_captured,
                            total_classified=self.classifier.total_classified,
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
                        "output_dir": self.output.get_output_dir(),
                        "counts": {
                            cam: {
                                "target": self.targets.get(cam, 0),
                                "captured": self.saved_counts.get(cam, 0),
                            }
                            for cam in CAMERA_TYPES
                        },
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

            await asyncio.sleep(self.interval)

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
            cam_type = classification["camera_type"]
            conf = classification.get("confidence", 0)
            logger.info(f"Frame #{self.total_classified_local}: {cam_type} (conf={conf:.2f})")

            target_for_type = self._adjusted_targets.get(cam_type, 0)
            current_saved = self.saved_counts.get(cam_type, 0)

            if target_for_type > 0 and current_saved < self.targets.get(cam_type, 0):
                filepath = await self.output.save_frame(
                    frame.jpeg_bytes, frame.video_time, classification, frame.video_part
                )
                self.saved_counts[cam_type] = self.saved_counts.get(cam_type, 0) + 1
                logger.info(f"Saved {filepath.name} ({cam_type}: {self.saved_counts[cam_type]}/{self.targets.get(cam_type, 0)})")

                # Record frame to SQLite for crash resilience
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
                    "camera_type": cam_type,
                    "confidence": classification["confidence"],
                    "saved": True,
                    "thumbnail_b64": thumbnail_b64,
                })
            else:
                await self.broadcast({
                    "type": "frame_skipped",
                    "video_time": frame.video_time,
                    "camera_type": cam_type,
                    "reason": "target_met",
                })

            if self.classifier.all_targets_met(self.saved_counts):
                logger.info("All targets met!")
                self.status = "completed"
                break

    async def _broadcast_loop(self):
        while self.status in ("capturing", "paused"):
            if self._stop_requested:
                break

            total_captured = sum(self.saved_counts.values())
            total_target = sum(self.targets.get(cam, 0) for cam in CAMERA_TYPES)

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
                    for cam in CAMERA_TYPES
                },
                "total_captured": total_captured,
                "total_target": total_target,
                "total_classified": self.classifier.total_classified,
                "api_cost": self.classifier.get_cost(),
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
        total_target = sum(self.targets.get(cam, 0) for cam in CAMERA_TYPES)

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
                for cam in CAMERA_TYPES
            },
            "total_captured": total_captured,
            "total_target": total_target,
            "total_classified": self.classifier.total_classified,
            "api_cost": self.classifier.get_cost(),
        }
