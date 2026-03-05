"""
Resample Runner — seek-capture loop for targeted re-capture.

Consumes resample_request.json targets. For each target:
  1. Seek to video_time_start - buffer
  2. Capture frames at configured interval until video_time_end
  3. Save with resample metadata (no classifier, no pre-filter)

Independent from Pipeline — much simpler capture flow.
"""

import asyncio
import base64
import io
import time
from typing import Callable, Awaitable, Optional

from PIL import Image

from backend.output_manager import OutputManager
from backend.sources.base import VideoSource
from backend.utils import logger


class ResampleRunner:
    def __init__(
        self,
        source: VideoSource,
        match: dict,
        targets: list[dict],
        settings: dict,
        broadcast_fn: Callable[[dict], Awaitable[None]],
        output_manager: OutputManager,
        task_id: str = "",
        resample_source_match: str = "",
        resample_request_file: str = "",
    ):
        self.source = source
        self.match = match
        self.targets = [t for t in targets if t.get("enabled", True)]
        self.settings = settings
        self.broadcast = broadcast_fn
        self.output = output_manager
        self.task_id = task_id
        self.resample_source_match = resample_source_match
        self.resample_request_file = resample_request_file

        self.interval = settings.get("interval", 0.3)
        self.seek_buffer = settings.get("seek_buffer", 2.0)
        self.thumbnail_width = 320

        # State
        self.status = "idle"
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_requested = False
        self._skip_target_requested = False

        # Progress tracking
        self._current_target_index = 0
        self._current_target_frame = 0
        self._current_target_total_est = 0
        self._saved_count = 0
        self._targets_completed = 0
        self._current_video_time = 0.0
        self._start_wall_time = 0.0

    async def run(self):
        self.status = "capturing"
        self._start_wall_time = time.time()
        total_targets = len(self.targets)

        await self.broadcast({
            "type": "resample_started",
            "task_id": self.task_id,
            "total_targets": total_targets,
        })

        try:
            is_local = self.source.get_source_name() == "local_file"

            for i, target in enumerate(self.targets):
                if self.status != "capturing" or self._stop_requested:
                    break

                self._current_target_index = i
                self._current_target_frame = 0
                self._skip_target_requested = False

                duration = target["video_time_end"] - target["video_time_start"]
                self._current_target_total_est = max(1, int(duration / self.interval))

                # Broadcast target start
                await self.broadcast({
                    "type": "resample_target_start",
                    "target_index": i,
                    "total_targets": total_targets,
                    "player_name": target.get("player_name", ""),
                    "camera_type": target.get("camera_type", ""),
                    "video_time_start": target["video_time_start"],
                    "video_time_end": target["video_time_end"],
                    "est_frames": self._current_target_total_est,
                })

                logger.info(
                    f"Resample target {i+1}/{total_targets}: "
                    f"{target.get('player_name', '?')} "
                    f"{target['video_time_start']:.1f}-{target['video_time_end']:.1f}s"
                )

                # Seek to start (with buffer)
                seek_time = max(0, target["video_time_start"] - self.seek_buffer)
                try:
                    await self.source.seek_to(seek_time)
                except Exception as e:
                    logger.warning(f"Seek failed for target {i}: {e}, skipping")
                    await self.broadcast({
                        "type": "resample_target_complete",
                        "target_index": i,
                        "frames_captured": 0,
                        "skipped": True,
                        "reason": f"Seek failed: {e}",
                    })
                    continue

                # Stabilization wait for web sources
                if not is_local:
                    await asyncio.sleep(1.0)

                # Build resample sequence ID
                original_seq_id = target.get("original_sequence_id", f"target_{i}")
                resample_seq_id = f"resample_{original_seq_id}_{i+1:03d}"

                # Capture loop for this target
                capture_time = target["video_time_start"]
                frame_in_target = 0

                while capture_time <= target["video_time_end"]:
                    if self._stop_requested or self.status != "capturing":
                        break
                    if self._skip_target_requested:
                        logger.info(f"Skipping target {i+1}")
                        break

                    # Handle pause
                    await self._pause_event.wait()
                    if self._stop_requested:
                        break

                    # For local files, seek to exact time
                    if is_local:
                        await self.source.seek_to(capture_time)

                    frame_bytes = await self.source.capture_frame()
                    if not frame_bytes:
                        if is_local:
                            break
                        await asyncio.sleep(0.2)
                        continue

                    video_time = await self.source.get_current_time()
                    if not is_local and self.source.current_part > 1:
                        video_time += self.source.part1_duration
                    self._current_video_time = video_time

                    # Camera angle from target metadata (known, no classifier needed)
                    camera_type = target.get("camera_type", "UNKNOWN")
                    classification = {
                        "classified_as": camera_type,
                        "confidence": 1.0,
                        "reasoning": "resample",
                    }

                    # Build per-frame sequence metadata (Instruction 2 schema)
                    sequence_meta = {
                        "camera_angle_source": "resample",
                        "sequence_id": resample_seq_id,
                        "sequence_type": "resample",
                        "sequence_purpose": target.get("reason", "annotation_gap"),
                        "sequence_position": frame_in_target,
                        "sequence_total_frames": None,  # backfilled at target end
                        "sequence_video_time_start": target["video_time_start"],
                        "sequence_video_time_end": target["video_time_end"],
                        "sequence_truncated": False,
                        "sequence_preempted_by": None,
                        "is_resample": True,
                        "resample_of": target.get("original_sequence_id"),
                        "resample_original_interval": target.get("original_interval"),
                    }

                    # Save frame
                    filepath = await self.output.save_frame(
                        frame_bytes, video_time, classification,
                        self.source.current_part,
                        sequence_meta=sequence_meta,
                    )

                    frame_in_target += 1
                    self._current_target_frame = frame_in_target
                    self._saved_count += 1

                    # Broadcast frame
                    thumbnail_b64 = self._make_thumbnail(frame_bytes)
                    await self.broadcast({
                        "type": "resample_frame",
                        "target_index": i,
                        "frame_in_target": frame_in_target,
                        "est_target_frames": self._current_target_total_est,
                        "video_time": video_time,
                        "camera_type": camera_type,
                        "thumbnail_b64": thumbnail_b64,
                        "total_captured": self._saved_count,
                        "targets_completed": self._targets_completed,
                        "total_targets": total_targets,
                    })

                    # Advance
                    capture_time += self.interval
                    if not is_local:
                        await asyncio.sleep(self.interval)

                # Target complete — backfill total_frames
                self.output.backfill_sequence(
                    sequence_id=resample_seq_id,
                    total_frames=frame_in_target,
                    video_time_end=target["video_time_end"],
                    truncated=self._skip_target_requested or self._stop_requested,
                )

                self._targets_completed += 1
                await self.broadcast({
                    "type": "resample_target_complete",
                    "target_index": i,
                    "frames_captured": frame_in_target,
                    "targets_completed": self._targets_completed,
                    "total_targets": total_targets,
                    "total_captured": self._saved_count,
                })

                logger.info(f"Resample target {i+1} complete: {frame_in_target} frames")

        except Exception as e:
            logger.exception(f"Resample error: {e}")
            await self.broadcast({"type": "error", "message": str(e)})
            self.status = "error"

        finally:
            # Generate output files
            self.output.generate_metadata_csv()
            duration = time.time() - self._start_wall_time

            self.output.generate_summary_json(
                classifier=None,
                duration_seconds=duration,
            )
            self.output.generate_frame_metadata_json(
                match_url=self.match.get("footballia_url"),
                is_resample=True,
                resample_source_match=self.resample_source_match,
                resample_request_file=self.resample_request_file,
            )

            if self.status != "error":
                self.status = "completed"

            summary = {
                "total_targets": len(self.targets),
                "targets_completed": self._targets_completed,
                "total_frames": self._saved_count,
                "duration_minutes": round(duration / 60, 1),
                "output_dir": self.output.get_output_dir(),
            }
            await self.broadcast({
                "type": "resample_completed",
                "summary": summary,
            })

            logger.info(
                f"Resample complete: {self._targets_completed}/{len(self.targets)} targets, "
                f"{self._saved_count} frames in {duration:.0f}s"
            )

            await self.source.close()

    # ── Controls ──

    def pause(self):
        self.status = "paused"
        self._pause_event.clear()
        logger.info("Resample paused")
        asyncio.create_task(self.broadcast({
            "type": "resample_status", "status": "paused",
        }))

    def resume(self):
        self.status = "capturing"
        self._pause_event.set()
        logger.info("Resample resumed")
        asyncio.create_task(self.broadcast({
            "type": "resample_status", "status": "capturing",
        }))

    def stop(self):
        self._stop_requested = True
        self.status = "completed"
        self._pause_event.set()
        logger.info("Resample stop requested")

    def skip_target(self):
        self._skip_target_requested = True
        logger.info(f"Skip target {self._current_target_index + 1} requested")

    def get_status(self) -> dict:
        return {
            "type": "resample_progress",
            "status": self.status,
            "task_id": self.task_id,
            "current_target": self._current_target_index,
            "total_targets": len(self.targets),
            "targets_completed": self._targets_completed,
            "current_target_frame": self._current_target_frame,
            "current_target_total_est": self._current_target_total_est,
            "total_captured": self._saved_count,
            "video_time": self._current_video_time,
        }

    # ── Helpers ──

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
