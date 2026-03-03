"""
Generic web video source — works with any site that has an HTML5 <video> element.

User workflow:
1. User provides a URL
2. Tool opens Chromium with --disable-gpu (DRM bypass)
3. User manually logs in and starts the video
4. User clicks "Video is playing" in the tool's UI
5. Tool finds the <video> element and begins capturing

DRM handling:
- On Windows/Linux: --disable-gpu forces CPU decoding, breaking the DRM chain
- On macOS: DRM bypass is unreliable; we detect black frames and warn the user
- Black frame detection uses PreFilter's brightness check

Does NOT handle:
- Automatic login (every site is different)
- Automatic video playback (user does this)
- Footballia-specific features (JWPlayer, multi-part, etc.)
"""
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional, Callable

from .base import VideoSource

logger = logging.getLogger(__name__)


class GenericWebSource(VideoSource):

    def __init__(self, config: dict):
        self._config = config
        self._playwright = None
        self._context = None
        self._page = None
        self._video_element = None
        self._is_ended: bool = False
        self._current_part = 1
        self._drm_warning_shown = False

    async def setup(self, url: str = "", broadcast_fn: Optional[Callable] = None, **kwargs) -> bool:
        """
        Launch browser and navigate to the URL.
        Returns True when the browser is open (does NOT wait for video — user handles that).
        """
        from playwright.async_api import async_playwright
        from backend.platform_utils import get_browser_profile_dir

        if broadcast_fn:
            await broadcast_fn({"type": "status", "message": "Launching browser..."})

        self._playwright = await async_playwright().start()

        # ── Browser launch arguments ──
        args = [
            "--disable-blink-features=AutomationControlled",
        ]

        # DRM bypass: disable GPU to force CPU video decoding
        args.extend([
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--disable-features=VizDisplayCompositor",
        ])

        if sys.platform == "darwin":
            logger.warning(
                "DRM bypass on macOS is unreliable. "
                "Screenshots of DRM-protected video may be black."
            )
            if broadcast_fn:
                await broadcast_fn({
                    "type": "warning",
                    "message": "macOS: DRM-protected videos may produce black screenshots. "
                               "If this happens, try using a local video file instead.",
                })

        profile_dir = get_browser_profile_dir() / "generic_web"

        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,  # Must be visible for user to log in
                args=args,
                viewport={"width": 1280, "height": 720},
                ignore_default_args=["--enable-automation"],
                accept_downloads=False,
            )
        except Exception as e:
            logger.error(f"Browser launch failed: {e}")
            if broadcast_fn:
                await broadcast_fn({"type": "error", "message": f"Browser launch failed: {e}"})
            return False

        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        if broadcast_fn:
            await broadcast_fn({"type": "status", "message": f"Navigating to {url}..."})

        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(f"Navigation warning: {e}")
            # Don't fail — page may still be loading

        if broadcast_fn:
            await broadcast_fn({
                "type": "waiting_for_user",
                "message": "Browser is open. Please log in (if needed) and start playing the video. "
                           "When the video is playing, click 'Video is playing' below.",
            })

        # Wait for user confirmation (this is handled by the server route —
        # setup() returns True here and a separate "confirm_video_ready" route
        # triggers find_video_element)
        return True

    async def find_video_element(self, broadcast_fn: Optional[Callable] = None) -> bool:
        """
        Find the <video> element on the page after user confirms video is playing.
        Called by the server when user clicks "Video is playing."
        """
        if broadcast_fn:
            await broadcast_fn({"type": "status", "message": "Searching for video player..."})

        # Strategy: find the largest <video> element on the page (including inside iframes)
        # Check main page first, then iframes
        video_found = False

        # Check main page
        videos = await self._page.query_selector_all("video")
        if videos:
            # Find the largest video by dimensions
            best_video = None
            best_area = 0
            for v in videos:
                bbox = await v.bounding_box()
                if bbox:
                    area = bbox["width"] * bbox["height"]
                    if area > best_area:
                        best_area = area
                        best_video = v
            if best_video and best_area > 10000:  # At least ~100x100 pixels
                self._video_element = best_video
                video_found = True

        # Check iframes if no video found on main page
        if not video_found:
            frames = self._page.frames
            for frame in frames:
                if frame == self._page.main_frame:
                    continue
                try:
                    videos = await frame.query_selector_all("video")
                    for v in videos:
                        bbox = await v.bounding_box()
                        if bbox and bbox["width"] * bbox["height"] > 10000:
                            self._video_element = v
                            video_found = True
                            break
                except Exception:
                    continue
                if video_found:
                    break

        if not video_found:
            logger.error("No video element found on the page")
            if broadcast_fn:
                await broadcast_fn({
                    "type": "error",
                    "message": "Could not find a video player. Make sure the video is visible and playing.",
                })
            return False

        # Check for DRM black frames
        test_frame = await self.capture_frame()
        if test_frame:
            from backend.pre_filter import PreFilter
            pf = PreFilter(enabled=True)
            result = pf.analyze(test_frame)
            if result["brightness"] < 15:
                if broadcast_fn:
                    await broadcast_fn({
                        "type": "warning",
                        "message": "DRM detected — screenshot is black. "
                                   "This platform's DRM protection prevents screenshots. "
                                   "Try: (1) Download the video and use Local File mode, "
                                   "or (2) On Windows/Linux, this sometimes resolves after a few seconds.",
                    })
                self._drm_warning_shown = True

        if broadcast_fn:
            await broadcast_fn({"type": "status", "message": "Video player found. Ready to capture."})

        return True

    async def capture_frame(self) -> Optional[bytes]:
        """Screenshot the video element."""
        if self._video_element is None:
            return None
        try:
            return await self._video_element.screenshot(type="jpeg", quality=90)
        except Exception as e:
            logger.warning(f"Frame capture failed: {e}")
            return None

    async def get_current_time(self) -> float:
        """Get video currentTime via JavaScript."""
        if self._video_element is None:
            return 0.0
        try:
            return await self._video_element.evaluate("v => v.currentTime")
        except Exception:
            return 0.0

    async def get_duration(self) -> float:
        if self._video_element is None:
            return 0.0
        try:
            dur = await self._video_element.evaluate("v => v.duration")
            return dur if dur and dur != float('inf') else 0.0
        except Exception:
            return 0.0

    async def is_ended(self) -> bool:
        if self._video_element is None:
            return True
        try:
            return await self._video_element.evaluate("v => v.ended")
        except Exception:
            return True

    async def seek_to(self, seconds: float) -> None:
        if self._video_element:
            try:
                await self._video_element.evaluate(f"v => v.currentTime = {seconds}")
                await asyncio.sleep(0.5)  # Wait for seek to settle
            except Exception as e:
                logger.warning(f"Seek failed: {e}")

    async def start_playback(self) -> None:
        """Attempt to resume playback via JS. May not work on all sites."""
        if self._video_element:
            try:
                await self._video_element.evaluate("v => v.play()")
            except Exception:
                pass

    async def handle_next_part(self) -> bool:
        """Generic web videos are single-part."""
        return False

    async def close(self) -> None:
        try:
            if self._context:
                await self._context.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._context = None
        self._playwright = None
        self._page = None
        self._video_element = None

    def get_source_name(self) -> str:
        return "generic_web"

    @property
    def current_part(self) -> int:
        return 1

    @property
    def total_parts(self) -> int:
        return 1

    @property
    def part1_duration(self) -> float:
        return 0.0  # Unknown until video loads

    def has_drm(self) -> bool:
        """We assume DRM is possible for generic web sources."""
        return True

    def requires_login(self) -> bool:
        return True  # User handles login manually
