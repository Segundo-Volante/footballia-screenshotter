"""
Local video file source — uses OpenCV (cv2) to read .mp4, .mkv, .avi, .mov files.

Advantages over web sources:
- No browser needed
- No network needed
- No DRM issues
- Precise seeking to any timestamp
- Fast frame extraction (10-50ms per frame vs 200-500ms for web screenshot)
- No login required

Requires: pip install opencv-python
"""
import logging
from pathlib import Path
from typing import Optional, Callable, Awaitable

from .base import VideoSource

logger = logging.getLogger(__name__)


class LocalFileSource(VideoSource):

    def __init__(self, config: dict):
        self._config = config
        self._filepath: Optional[Path] = None
        self._cap = None  # cv2.VideoCapture
        self._fps: float = 25.0
        self._total_frames: int = 0
        self._duration: float = 0.0
        self._current_time: float = 0.0
        self._ended: bool = False

    async def setup(self, filepath: str = "", broadcast_fn: Optional[Callable] = None, **kwargs) -> bool:
        """
        Open a local video file.

        Args:
            filepath: Path to the video file (.mp4, .mkv, .avi, .mov)
            broadcast_fn: Optional callback for status messages

        Returns:
            True if file opened successfully.
        """
        try:
            import cv2
        except ImportError:
            logger.error("opencv-python not installed. Run: pip install opencv-python")
            if broadcast_fn:
                await broadcast_fn({
                    "type": "error",
                    "message": "opencv-python not installed. Run: pip install opencv-python",
                })
            return False

        self._filepath = Path(filepath)
        if not self._filepath.exists():
            logger.error(f"File not found: {self._filepath}")
            if broadcast_fn:
                await broadcast_fn({
                    "type": "error",
                    "message": f"File not found: {self._filepath}",
                })
            return False

        if broadcast_fn:
            await broadcast_fn({"type": "status", "message": f"Opening {self._filepath.name}..."})

        self._cap = cv2.VideoCapture(str(self._filepath))
        if not self._cap.isOpened():
            logger.error(f"Failed to open video: {self._filepath}")
            if broadcast_fn:
                await broadcast_fn({"type": "error", "message": "Failed to open video file"})
            return False

        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 25.0
        self._total_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._duration = self._total_frames / self._fps if self._fps > 0 else 0.0

        if broadcast_fn:
            mins = int(self._duration // 60)
            secs = int(self._duration % 60)
            width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            await broadcast_fn({
                "type": "status",
                "message": f"Opened: {self._filepath.name} ({mins}:{secs:02d}, {width}×{height}, {self._fps:.1f}fps)",
            })

        self._ended = False
        return True

    async def capture_frame(self) -> Optional[bytes]:
        """Read current frame and encode as JPEG bytes."""
        import cv2
        if self._cap is None or not self._cap.isOpened():
            return None

        ret, frame = self._cap.read()
        if not ret:
            self._ended = True
            return None

        # Update current time from actual position
        self._current_time = self._cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0

        # Encode as JPEG
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, 90]
        success, buffer = cv2.imencode('.jpg', frame, encode_params)
        if not success:
            return None

        return buffer.tobytes()

    async def get_current_time(self) -> float:
        if self._cap:
            self._current_time = self._cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        return self._current_time

    async def get_duration(self) -> float:
        return self._duration

    async def is_ended(self) -> bool:
        return self._ended

    async def seek_to(self, seconds: float) -> None:
        """Seek to a specific timestamp."""
        if self._cap:
            import cv2
            self._cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000.0)
            self._current_time = seconds
            self._ended = False

    async def start_playback(self) -> None:
        """For local files, playback is implicit — we read frames on demand.
        The pipeline controls timing via asyncio.sleep between reads."""
        pass

    async def handle_next_part(self) -> bool:
        """Local files are single-part. Always returns False."""
        return False

    async def close(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None

    def get_source_name(self) -> str:
        return "local_file"

    @property
    def current_part(self) -> int:
        return 1

    @property
    def total_parts(self) -> int:
        return 1

    @property
    def part1_duration(self) -> float:
        return self._duration

    def has_drm(self) -> bool:
        return False

    def requires_login(self) -> bool:
        return False
