import asyncio
from pathlib import Path

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

from backend.utils import logger

# Persistent profile directory so login sessions survive across runs
PROFILE_DIR = Path(".browser_profile")


class BrowserEngine:
    def __init__(self, config: dict):
        self.config = config
        self._playwright = None
        self._browser = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._video_frame = None  # The frame (main or iframe) containing the video
        self._video_element = None
        self._current_part = 1
        self._total_parts = 1
        self._part1_duration = 0.0

    @property
    def current_part(self) -> int:
        return self._current_part

    @property
    def total_parts(self) -> int:
        return self._total_parts

    @property
    def part1_duration(self) -> float:
        return self._part1_duration

    async def launch(self):
        PROFILE_DIR.mkdir(exist_ok=True)
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=self.config.get("headless", False),
            viewport={
                "width": self.config.get("viewport_width", 1280),
                "height": self.config.get("viewport_height", 720),
            },
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Use the default page or create one
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()
        logger.info("Browser launched with persistent profile")

    async def navigate_to_match(self, url: str) -> bool:
        timeout = self.config.get("timeout_ms", 30000)
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            logger.info(f"Navigated to {url}")
            await asyncio.sleep(3)
            return True
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            return False

    async def is_login_required(self) -> bool:
        """Check if Footballia is showing a login wall."""
        try:
            # Positive check: login wall alert present
            alert = await self._page.query_selector(".alert")
            if alert:
                text = await alert.inner_text()
                if "sign up" in text.lower() or "log in" in text.lower():
                    return True

            # Negative checks: if any video player exists, user is logged in
            # JWPlayer on main page
            for sel in ["#mediaplayer", ".jwplayer", "video", ".embed-responsive iframe"]:
                el = await self._page.query_selector(sel)
                if el:
                    return False

            # No login wall found, but also no video player — likely still needs login
            return True
        except Exception:
            pass
        return False

    async def wait_for_login(self, broadcast_fn=None, timeout=300):
        """
        Wait for the user to log in via the Playwright browser window.
        Polls every 3 seconds for up to `timeout` seconds.
        """
        logger.info("Login required — waiting for user to log in via browser window")
        if broadcast_fn:
            await broadcast_fn({
                "type": "status",
                "status": "capturing",
                "message": "Login required. Please log in via the browser window that opened.",
            })

        elapsed = 0
        while elapsed < timeout:
            needs_login = await self.is_login_required()
            if not needs_login:
                logger.info("User logged in successfully")
                if broadcast_fn:
                    await broadcast_fn({
                        "type": "status",
                        "status": "capturing",
                        "message": "Logged in! Finding video player...",
                    })
                return True
            if elapsed % 15 == 0:
                logger.debug(f"Still waiting for login... ({elapsed}s elapsed)")
            await asyncio.sleep(3)
            elapsed += 3

        logger.error("Login timeout")
        return False

    async def detect_parts(self):
        """Check if the match page says it's divided into 2 files."""
        try:
            info_text = await self._page.inner_text("body")
            if "divided in 2 files" in info_text or "dividido en 2" in info_text:
                self._total_parts = 2
                logger.info("Match is divided into 2 parts")
            else:
                self._total_parts = 1
        except Exception:
            self._total_parts = 1

    def _is_social_iframe(self, src: str) -> bool:
        """Check if an iframe src belongs to a social media widget (not a video player)."""
        if not src:
            return False
        skip_domains = [
            "twitter.com", "platform.twitter.com", "x.com",
            "facebook.com", "instagram.com", "tiktok.com",
            "google.com/recaptcha", "doubleclick.net", "googlesyndication.com",
        ]
        return any(domain in src for domain in skip_domains)

    async def find_video_element(self) -> bool:
        timeout = self.config.get("timeout_ms", 30000)

        # Strategy 1: Look for JWPlayer or video directly on the main page
        # Footballia uses JWPlayer which renders <video> on the main page
        try:
            # Try JWPlayer-specific selectors first
            for selector in [
                "#mediaplayer video", ".jwplayer video",
                "#jwplayer video", "video[src*='footballia']",
            ]:
                video = await self._page.query_selector(selector)
                if video:
                    size = await video.evaluate(
                        "el => ({w: el.videoWidth || el.offsetWidth, h: el.videoHeight || el.offsetHeight, src: el.src || el.querySelector('source')?.src || ''})"
                    )
                    if size.get("w", 0) > 200:
                        self._video_element = video
                        self._video_frame = self._page
                        logger.info(f"Found JWPlayer video on main page (size: {size})")
                        return True

            # Try any video on main page
            videos = await self._page.query_selector_all("video")
            for video in videos:
                size = await video.evaluate(
                    "el => ({w: el.videoWidth || el.offsetWidth, h: el.videoHeight || el.offsetHeight, src: el.src || el.querySelector('source')?.src || ''})"
                )
                if size.get("w", 0) > 200:
                    self._video_element = video
                    self._video_frame = self._page
                    logger.info(f"Found video on main page (size: {size})")
                    return True
        except Exception as e:
            logger.debug(f"No video on main page: {e}")

        # Strategy 2: Look for video inside embed iframes (skip social media)
        try:
            iframes = await self._page.query_selector_all(
                ".embed-responsive iframe, iframe[src*='video'], iframe[src*='player'], iframe[allowfullscreen]"
            )
            for iframe_el in iframes:
                src = await iframe_el.get_attribute("src") or ""
                if self._is_social_iframe(src):
                    logger.debug(f"Skipping social iframe: {src[:80]}")
                    continue
                logger.info(f"Checking embed iframe: {src[:80]}")
                frame = await iframe_el.content_frame()
                if frame:
                    try:
                        video = await frame.wait_for_selector("video", timeout=10000)
                        if video:
                            size = await video.evaluate(
                                "el => ({w: el.videoWidth || el.offsetWidth, h: el.videoHeight || el.offsetHeight})"
                            )
                            if size.get("w", 0) > 200 and size.get("h", 0) > 100:
                                self._video_element = video
                                self._video_frame = frame
                                logger.info(f"Found video in embed iframe (size: {size})")
                                return True
                    except Exception:
                        logger.debug(f"No video in iframe: {src[:60]}")
        except Exception as e:
            logger.debug(f"No embed iframes found: {e}")

        # Strategy 3: Try remaining iframes (non-social)
        for frame in self._page.frames:
            if frame == self._page.main_frame:
                continue
            frame_url = frame.url or ""
            if self._is_social_iframe(frame_url):
                continue
            try:
                video = await frame.wait_for_selector("video", timeout=3000)
                if video:
                    size = await video.evaluate(
                        "el => ({w: el.videoWidth || el.offsetWidth, h: el.videoHeight || el.offsetHeight})"
                    )
                    if size.get("w", 0) > 200 and size.get("h", 0) > 100:
                        self._video_element = video
                        self._video_frame = frame
                        logger.info(f"Found video in iframe (size: {size})")
                        return True
            except Exception:
                continue

        logger.error("No video element found")
        return False

    async def start_playback(self):
        frame = self._video_frame or self._page

        # Try clicking JWPlayer's play button overlay first
        for selector in [
            "#jwplayer_display_button_play",
            ".jw-icon-display",
            ".jw-display-icon-container",
            ".jwdisplay",
        ]:
            try:
                btn = await frame.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click(timeout=5000)
                    await asyncio.sleep(1)
                    if not await self.is_video_paused():
                        logger.info(f"Playback started via JWPlayer button ({selector})")
                        return
            except Exception:
                continue

        # Try clicking the video element with force (bypasses overlay interception)
        try:
            await self._video_element.click(force=True, timeout=5000)
            await asyncio.sleep(1)
            if not await self.is_video_paused():
                logger.info("Playback started via force-click on video")
                return
        except Exception as e:
            logger.debug(f"Force-click failed: {e}")

        # Fall back to JavaScript play()
        try:
            await self._video_element.evaluate("el => { if (el.paused) el.play(); }")
            await asyncio.sleep(1)
            if not await self.is_video_paused():
                logger.info("Playback started via JS play()")
                return
        except Exception:
            pass

        # Last resort: find any video in frame and play it
        try:
            await frame.evaluate("document.querySelector('video')?.play()")
            logger.info("Playback started via generic JS play()")
        except Exception as e:
            logger.warning(f"All playback methods failed: {e}")

    async def seek_to(self, seconds: float):
        try:
            await self._video_element.evaluate(
                f"el => {{ el.currentTime = {seconds}; }}"
            )
            await asyncio.sleep(1)
            logger.info(f"Seeked to {seconds}s")
        except Exception as e:
            logger.error(f"Seek failed: {e}")

    async def get_video_time(self) -> float:
        try:
            return float(await self._video_element.evaluate("el => el.currentTime"))
        except Exception:
            return 0.0

    async def get_video_duration(self) -> float:
        try:
            dur = await self._video_element.evaluate("el => el.duration")
            return float(dur) if dur and dur != float("inf") else 0.0
        except Exception:
            return 0.0

    async def is_video_ended(self) -> bool:
        try:
            return bool(await self._video_element.evaluate("el => el.ended"))
        except Exception:
            return False

    async def is_video_paused(self) -> bool:
        try:
            return bool(await self._video_element.evaluate("el => el.paused"))
        except Exception:
            return True

    async def pause_video(self):
        try:
            await self._video_element.evaluate("el => el.pause()")
        except Exception:
            pass

    async def resume_video(self):
        try:
            await self._video_element.evaluate("el => { if (el.paused) el.play(); }")
        except Exception:
            pass

    async def screenshot_video(self) -> bytes | None:
        try:
            return await self._video_element.screenshot(type="jpeg", quality=85)
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return None

    async def handle_video_end_and_next_part(self) -> bool:
        logger.info("Video ended, looking for part 2...")
        self._part1_duration = await self.get_video_duration()

        # Footballia says: "Let the first video play till the end without stopping it
        # and the following video will begin automatically."
        # Wait for auto-load
        for wait_round in range(6):
            await asyncio.sleep(5)

            # Check if current video restarted (new source loaded)
            try:
                ended = await self._video_element.evaluate("el => el.ended")
                if not ended:
                    current_time = await self.get_video_time()
                    if current_time < 10:
                        self._current_part = 2
                        self._total_parts = 2
                        logger.info("Part 2 auto-loaded (video reset)")
                        return True
                # Check if source changed
                dur = await self.get_video_duration()
                if dur != self._part1_duration and dur > 0:
                    self._current_part = 2
                    self._total_parts = 2
                    logger.info("Part 2 auto-loaded (duration changed)")
                    return True
            except Exception:
                pass

            # Re-search for a new video element
            try:
                frame = self._video_frame or self._page
                videos = await frame.query_selector_all("video")
                for v in videos:
                    ended = await v.evaluate("el => el.ended")
                    if not ended:
                        ct = await v.evaluate("el => el.currentTime")
                        if ct < 10:
                            self._video_element = v
                            self._current_part = 2
                            self._total_parts = 2
                            await self.start_playback()
                            logger.info("Found new video element for part 2")
                            return True
            except Exception:
                pass

        # Try clicking next-part links on the main page
        try:
            main_page = self._context.pages[0] if self._context else self._page
            for selector in [
                'a:has-text("Part 2")', 'a:has-text("part 2")',
                'a:has-text("Parte 2")', 'a:has-text("siguiente")',
            ]:
                try:
                    el = await main_page.query_selector(selector)
                    if el and await el.is_visible():
                        await el.click()
                        await asyncio.sleep(5)
                        if await self.find_video_element():
                            self._current_part = 2
                            self._total_parts = 2
                            await self.start_playback()
                            logger.info(f"Found part 2 via link")
                            return True
                except Exception:
                    continue
        except Exception:
            pass

        logger.info("No part 2 found — match complete")
        return False

    async def close(self):
        try:
            if self._context:
                await self._context.close()
            if self._playwright:
                await self._playwright.stop()
            logger.info("Browser closed")
        except Exception as e:
            logger.warning(f"Browser close issue: {e}")
