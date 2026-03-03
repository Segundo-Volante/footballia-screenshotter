"""
Abstract base class for video sources.

Part 1 implements FootballiaSource.
Future parts add:
  - LocalFileSource (Part 4, uses OpenCV)
  - GenericWebSource (Part 4, any website with <video> + DRM handling)
"""
from abc import ABC, abstractmethod
from typing import Optional, Callable, Awaitable


class VideoSource(ABC):
    """All video sources implement this interface."""

    @abstractmethod
    async def setup(self, broadcast_fn: Optional[Callable] = None) -> bool:
        """Initialize the source. Returns True if ready to capture.
        For web sources: launch browser, navigate, handle login, find video.
        For local files: open file, get metadata.
        """

    @abstractmethod
    async def capture_frame(self) -> Optional[bytes]:
        """Capture current frame as JPEG bytes. Returns None on failure."""

    @abstractmethod
    async def get_current_time(self) -> float:
        """Current playback position in seconds."""

    @abstractmethod
    async def get_duration(self) -> float:
        """Total video duration in seconds."""

    @abstractmethod
    async def is_ended(self) -> bool:
        """True if video has finished playing."""

    @abstractmethod
    async def seek_to(self, seconds: float) -> None:
        """Seek to a specific time."""

    @abstractmethod
    async def start_playback(self) -> None:
        """Begin or resume video playback."""

    @abstractmethod
    async def handle_next_part(self) -> bool:
        """Handle multi-part videos (e.g. Footballia's 2-part matches).
        Returns True if a next part was found and loaded."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources."""

    @abstractmethod
    def get_source_name(self) -> str:
        """Return source identifier, e.g. 'footballia', 'local_file', 'generic_web'."""

    @property
    @abstractmethod
    def current_part(self) -> int:
        """Current video part number (1-indexed)."""

    @property
    @abstractmethod
    def total_parts(self) -> int:
        """Total number of video parts."""

    @property
    @abstractmethod
    def part1_duration(self) -> float:
        """Duration of part 1 (for time offset calculation in multi-part videos)."""

    def has_drm(self) -> bool:
        """True if this source may have DRM protection."""
        return False

    def requires_login(self) -> bool:
        """True if user interaction is needed before capture can begin."""
        return False
