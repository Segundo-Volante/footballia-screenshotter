import asyncio
import base64
import io
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from PIL import Image

from backend.browser_engine import BrowserEngine
from backend.camera_classifier import CameraClassifier
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
        match: dict,
        targets: dict,
        start_time: str,
        config: dict,
        broadcast_fn: Callable[[dict], Awaitable[None]],
    ):
        self.match = match
        self.targets = targets
        self.start_time_str = start_time
        self.config = config
        self.broadcast = broadcast_fn

        self.browser = BrowserEngine(config["browser"])
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
        self._pause_event.set()  # Not paused initially
        self._stop_requested = False
        self.total_classified_local = 0

        # Saved counts (separate from classified counts)
        self.saved_counts: dict[str, int] = {t: 0 for t in CAMERA_TYPES}

        # Account for existing captures (resume support)
        existing = self.output.get_existing_counts()
        for cam, count in existing.items():
            self.saved_counts[cam] = count
            # Reduce targets so we don't re-collect
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
            # Launch browser
            await self.broadcast({"type": "status", "status": "capturing", "message": "Launching browser..."})
            await self.browser.launch()

            # Navigate
            url = self.match.get("footballia_url", "")
            await self.broadcast({"type": "status", "status": "capturing", "message": "Navigating to match..."})
            if not await self.browser.navigate_to_match(url):
                await self.broadcast({"type": "error", "message": "Failed to navigate to match URL"})
                self.status = "error"
                return

            # Check if login is required
            if await self.browser.is_login_required():
                logged_in = await self.browser.wait_for_login(
                    broadcast_fn=self.broadcast, timeout=300
                )
                if not logged_in:
                    await self.broadcast({
                        "type": "error",
                        "message": "Login timeout. Please log in via the browser window and try again.",
                    })
                    self.status = "error"
                    return
                # Reload the page after login to get the video player
                await self.browser.navigate_to_match(url)
                await asyncio.sleep(3)

            # Detect multi-part matches
            await self.browser.detect_parts()

            # Find video
            await self.broadcast({"type": "status", "status": "capturing", "message": "Finding video player..."})
            if not await self.browser.find_video_element():
                await self.broadcast({
                    "type": "error",
                    "message": "Video player not found. Try reloading the page or check if the match video is available.",
                })
                self.status = "error"
                return

            # Start playback
            await self.broadcast({"type": "status", "status": "capturing", "message": "Starting video playback..."})
            await self.browser.start_playback()
            await asyncio.sleep(2)

            # Verify playback actually started
            is_paused = await self.browser.is_video_paused()
            current_time = await self.browser.get_video_time()
            logger.info(f"After start_playback: paused={is_paused}, time={current_time:.1f}s")

            if is_paused:
                logger.warning("Video still paused after start_playback, retrying...")
                await self.browser.start_playback()
                await asyncio.sleep(2)
                is_paused = await self.browser.is_video_paused()
                logger.info(f"After retry: paused={is_paused}")

            # Get duration
            self._video_duration = await self.browser.get_video_duration()
            logger.info(f"Video duration: {self._video_duration:.1f}s")

            # Seek if needed
            start_seconds = parse_time(self.start_time_str)
            if start_seconds > 0:
                await self.browser.seek_to(start_seconds)
                await asyncio.sleep(1)

            await self.broadcast({"type": "status", "status": "capturing", "message": "Capturing frames..."})

            # Run three concurrent loops
            await asyncio.gather(
                self._capture_loop(),
                self._classify_loop(),
                self._broadcast_loop(),
            )

        except Exception as e:
            logger.exception(f"Pipeline error: {e}")
            await self.broadcast({"type": "error", "message": str(e)})
            self.status = "error"
        finally:
            # Save reports
            self.output.generate_metadata_csv()
            duration = time.time() - self._start_wall_time
            self.output.generate_summary_json(self.classifier, duration)

            if self.status != "error":
                self.status = "completed"
                total_captured = sum(self.saved_counts.values())
                total_target = sum(self.targets.get(cam, 0) for cam in CAMERA_TYPES)
                await self.broadcast({
                    "type": "completed",
                    "summary": {
                        "total_captured": total_captured,
                        "total_target": total_target,
                        "duration_minutes": round(duration / 60, 1),
                        "api_cost": round(self.classifier.get_cost(), 6),
                        "output_dir": self.output.output_dir,
                        "counts": {
                            cam: {
                                "target": self.targets.get(cam, 0),
                                "captured": self.saved_counts.get(cam, 0),
                            }
                            for cam in CAMERA_TYPES
                        },
                    },
                })

            await self.browser.close()

    async def _capture_loop(self):
        while self.status == "capturing":
            # Check pause
            await self._pause_event.wait()
            if self._stop_requested or self.status != "capturing":
                break

            # Check if video ended
            if await self.browser.is_video_ended():
                found_next = await self.browser.handle_video_end_and_next_part()
                if not found_next:
                    logger.info("Video ended, no more parts")
                    self.status = "completed"
                    break
                self._video_duration = await self.browser.get_video_duration()
                continue

            # Capture screenshot
            jpeg_bytes = await self.browser.screenshot_video()
            if jpeg_bytes:
                video_time = await self.browser.get_video_time()
                # Offset for part 2
                if self.browser.current_part > 1:
                    video_time += self.browser.part1_duration

                self._current_video_time = video_time

                frame = CapturedFrame(
                    jpeg_bytes=jpeg_bytes,
                    video_time=video_time,
                    video_part=self.browser.current_part,
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

            # Classify
            self.total_classified_local += 1
            logger.info(f"Classifying frame #{self.total_classified_local} at {format_time(frame.video_time)}...")
            classification = await self.classifier.classify_frame(frame.jpeg_bytes)
            cam_type = classification["camera_type"]
            conf = classification.get("confidence", 0)
            logger.info(f"Frame #{self.total_classified_local}: {cam_type} (conf={conf:.2f})")

            target_for_type = self._adjusted_targets.get(cam_type, 0)
            current_saved = self.saved_counts.get(cam_type, 0)

            if target_for_type > 0 and current_saved < self.targets.get(cam_type, 0):
                # Save frame
                filepath = await self.output.save_frame(
                    frame.jpeg_bytes, frame.video_time, classification, frame.video_part
                )
                self.saved_counts[cam_type] = self.saved_counts.get(cam_type, 0) + 1
                logger.info(f"Saved {filepath.name} ({cam_type}: {self.saved_counts[cam_type]}/{self.targets.get(cam_type, 0)})")

                # Generate thumbnail
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

            # Check if all targets met
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
                    self.browser.part1_duration if self.browser.current_part > 1 else 0
                ),
                "video_part": self.browser.current_part,
                "total_parts": self.browser.total_parts,
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
        self._pause_event.set()  # Unblock if paused
        logger.info("Stop requested")

    def get_status(self) -> dict:
        total_captured = sum(self.saved_counts.values())
        total_target = sum(self.targets.get(cam, 0) for cam in CAMERA_TYPES)

        return {
            "type": "progress",
            "video_time": self._current_video_time,
            "video_duration": self._video_duration,
            "video_part": self.browser.current_part,
            "total_parts": self.browser.total_parts,
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
