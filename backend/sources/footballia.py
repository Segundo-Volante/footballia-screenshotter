"""
Footballia video source — Playwright-based browser automation for footballia.eu.
Implements the VideoSource interface.
"""
import asyncio
from pathlib import Path
from typing import Optional, Callable, Awaitable

from playwright.async_api import async_playwright, Page, BrowserContext

from backend.sources.base import VideoSource
from backend.footballia_scraper import FootballiaScraper
from backend.lineup_scraper import scrape_lineup
from backend.utils import logger

PROFILE_DIR = Path(".browser_profile")


class FootballiaSource(VideoSource):
    def __init__(self, config: dict):
        self.config = config
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._video_frame = None
        self._video_element = None
        self._current_part = 1
        self._total_parts = 1
        self._part1_duration = 0.0
        self._url: str = ""
        self._scraper = FootballiaScraper()
        self.match_data: dict = {}  # Scraped page data
        self.lineup_data: dict | None = None  # Structured lineup from lineup_scraper

    # ── VideoSource properties ──

    @property
    def current_part(self) -> int:
        return self._current_part

    @property
    def total_parts(self) -> int:
        return self._total_parts

    @property
    def part1_duration(self) -> float:
        return self._part1_duration

    def get_source_name(self) -> str:
        return "footballia"

    # ── VideoSource interface ──

    async def setup(self, broadcast_fn: Optional[Callable] = None, url: str = "", navigate_only: bool = False) -> bool:
        """Full setup sequence: launch → navigate → login → detect parts → find video → start playback.

        Args:
            broadcast_fn: Status callback
            url: Footballia match or person page URL
            navigate_only: If True, just open the page without searching for video player.
                           Used by the Navigator for scraping person pages.
        """
        self._url = url

        # Launch browser
        if broadcast_fn:
            await broadcast_fn({"type": "status", "status": "capturing", "message": "Launching browser..."})
        await self._launch()

        # Navigate
        if broadcast_fn:
            await broadcast_fn({"type": "status", "status": "capturing", "message": "Navigating to match..."})
        if not await self._navigate(url):
            return False

        if navigate_only:
            # Don't search for JWPlayer, don't try to play video
            return True

        # Handle login
        if await self._is_login_required():
            logged_in = await self._wait_for_login(broadcast_fn=broadcast_fn, timeout=300)
            if not logged_in:
                if broadcast_fn:
                    await broadcast_fn({
                        "type": "error",
                        "message": "Login timeout. Please log in via the browser window and try again.",
                    })
                return False
            await self._navigate(url)
            await asyncio.sleep(3)

        # ── Scrape match page data ──
        if broadcast_fn:
            await broadcast_fn({"type": "status", "status": "capturing", "message": "Extracting match information..."})

        try:
            self.match_data = await self._scraper.scrape_match_page(self._page)
            self._scraper.resolve_goal_teams(self.match_data)

            if broadcast_fn and self.match_data.get("scrape_success"):
                info_parts = []
                if self.match_data["home_lineup"]:
                    info_parts.append(f"{len(self.match_data['home_lineup'])} + {len(self.match_data['away_lineup'])} players")
                if self.match_data["goals"]:
                    info_parts.append(f"{len(self.match_data['goals'])} goals")
                if info_parts:
                    await broadcast_fn({
                        "type": "match_info",
                        "data": self.match_data,
                        "summary": f"Found: {', '.join(info_parts)}",
                    })
        except Exception as e:
            logger.warning(f"Scraper failed (non-fatal): {e}")
            self.match_data = {"scrape_success": False}

        # ── Scrape structured lineup data ──
        try:
            self.lineup_data = await scrape_lineup(self._page, url)
            if self.lineup_data and broadcast_fn:
                home_count = len(self.lineup_data["home_team"]["players"])
                away_count = len(self.lineup_data["away_team"]["players"])
                await broadcast_fn({
                    "type": "lineup_scraped",
                    "home_players": home_count,
                    "away_players": away_count,
                    "message": f"Lineup found: {home_count} home, {away_count} away players",
                })
            elif not self.lineup_data and broadcast_fn:
                await broadcast_fn({
                    "type": "lineup_scraped",
                    "home_players": 0,
                    "away_players": 0,
                    "message": "Lineup not available for this match",
                })
        except Exception as e:
            logger.warning(f"Lineup scraping failed (non-fatal): {e}")
            self.lineup_data = None

        # Detect parts
        await self._detect_parts()

        # Find video
        if broadcast_fn:
            await broadcast_fn({"type": "status", "status": "capturing", "message": "Finding video player..."})
        if not await self._find_video_element():
            return False

        # Start playback
        if broadcast_fn:
            await broadcast_fn({"type": "status", "status": "capturing", "message": "Starting video playback..."})
        await self.start_playback()
        await asyncio.sleep(2)

        # Verify playback
        is_paused = await self._is_video_paused()
        current_time = await self.get_current_time()
        logger.info(f"After start_playback: paused={is_paused}, time={current_time:.1f}s")

        if is_paused:
            logger.warning("Video still paused after start_playback, retrying...")
            await self.start_playback()
            await asyncio.sleep(2)

        return True

    async def capture_frame(self) -> Optional[bytes]:
        try:
            return await self._video_element.screenshot(type="jpeg", quality=85)
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return None

    async def get_current_time(self) -> float:
        try:
            return float(await self._video_element.evaluate("el => el.currentTime"))
        except Exception:
            return 0.0

    async def get_duration(self) -> float:
        try:
            dur = await self._video_element.evaluate("el => el.duration")
            return float(dur) if dur and dur != float("inf") else 0.0
        except Exception:
            return 0.0

    async def is_ended(self) -> bool:
        try:
            return bool(await self._video_element.evaluate("el => el.ended"))
        except Exception:
            return False

    async def seek_to(self, seconds: float) -> None:
        try:
            await self._video_element.evaluate(
                f"el => {{ el.currentTime = {seconds}; }}"
            )
            await asyncio.sleep(1)
            logger.info(f"Seeked to {seconds}s")
        except Exception as e:
            logger.error(f"Seek failed: {e}")

    async def start_playback(self) -> None:
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
                    if not await self._is_video_paused():
                        logger.info(f"Playback started via JWPlayer button ({selector})")
                        return
            except Exception:
                continue

        # Try clicking the video element with force
        try:
            await self._video_element.click(force=True, timeout=5000)
            await asyncio.sleep(1)
            if not await self._is_video_paused():
                logger.info("Playback started via force-click on video")
                return
        except Exception as e:
            logger.debug(f"Force-click failed: {e}")

        # Fall back to JavaScript play()
        try:
            await self._video_element.evaluate("el => { if (el.paused) el.play(); }")
            await asyncio.sleep(1)
            if not await self._is_video_paused():
                logger.info("Playback started via JS play()")
                return
        except Exception:
            pass

        # Last resort
        try:
            await frame.evaluate("document.querySelector('video')?.play()")
            logger.info("Playback started via generic JS play()")
        except Exception as e:
            logger.warning(f"All playback methods failed: {e}")

    async def handle_next_part(self) -> bool:
        logger.info("Video ended, looking for part 2...")
        self._part1_duration = await self.get_duration()

        for wait_round in range(6):
            await asyncio.sleep(5)
            try:
                ended = await self._video_element.evaluate("el => el.ended")
                if not ended:
                    current_time = await self.get_current_time()
                    if current_time < 10:
                        self._current_part = 2
                        self._total_parts = 2
                        logger.info("Part 2 auto-loaded (video reset)")
                        return True
                dur = await self.get_duration()
                if dur != self._part1_duration and dur > 0:
                    self._current_part = 2
                    self._total_parts = 2
                    logger.info("Part 2 auto-loaded (duration changed)")
                    return True
            except Exception:
                pass

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

        # Try clicking next-part links
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
                        if await self._find_video_element():
                            self._current_part = 2
                            self._total_parts = 2
                            await self.start_playback()
                            logger.info("Found part 2 via link")
                            return True
                except Exception:
                    continue
        except Exception:
            pass

        logger.info("No part 2 found — match complete")
        return False

    async def close(self) -> None:
        try:
            if self._context:
                await self._context.close()
            if self._playwright:
                await self._playwright.stop()
            logger.info("Browser closed")
        except Exception as e:
            logger.warning(f"Browser close issue: {e}")

    # ── Internal Footballia-specific methods ──

    async def _launch(self):
        PROFILE_DIR.mkdir(exist_ok=True)

        # Clean stale SingletonLock if the owning process is dead
        lock_file = PROFILE_DIR / "SingletonLock"
        if lock_file.exists() or lock_file.is_symlink():
            try:
                import os, signal, platform as _plat
                pid = None
                if _plat.system() != "Windows" and lock_file.is_symlink():
                    # Unix: lock is a symlink "hostname-pid"
                    target = os.readlink(str(lock_file))
                    pid_str = target.rsplit("-", 1)[-1]
                    pid = int(pid_str)
                elif _plat.system() == "Windows" and lock_file.is_file():
                    # Windows: lock is a regular file containing the PID
                    try:
                        content = lock_file.read_text(encoding="utf-8").strip()
                        pid_str = content.rsplit("-", 1)[-1]
                        pid = int(pid_str)
                    except (ValueError, OSError):
                        pid = None

                if pid is not None:
                    if _plat.system() == "Windows":
                        # Windows: use taskkill; os.kill(pid, 0) would terminate
                        import subprocess
                        ret = subprocess.run(
                            ["tasklist", "/FI", f"PID eq {pid}"],
                            capture_output=True, text=True,
                        )
                        if str(pid) in ret.stdout:
                            logger.warning(f"Killing stale browser process {pid}")
                            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                           capture_output=True)
                    else:
                        try:
                            os.kill(pid, 0)  # Check if alive
                            logger.warning(f"Killing stale browser process {pid}")
                            os.kill(pid, signal.SIGTERM)
                            import time as _time
                            _time.sleep(1)
                            try:
                                os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        except ProcessLookupError:
                            pass  # Already dead

                lock_file.unlink(missing_ok=True)
                logger.info("Removed stale SingletonLock")
            except Exception as e:
                # If we can't parse it, just remove it
                logger.warning(f"Removing unparseable SingletonLock: {e}")
                lock_file.unlink(missing_ok=True)

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
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()
        logger.info("Browser launched with persistent profile")

    async def _navigate(self, url: str) -> bool:
        timeout = self.config.get("timeout_ms", 30000)
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            logger.info(f"Navigated to {url}")
            await asyncio.sleep(3)
            return True
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            return False

    async def _is_login_required(self) -> bool:
        try:
            alert = await self._page.query_selector(".alert")
            if alert:
                text = await alert.inner_text()
                if "sign up" in text.lower() or "log in" in text.lower():
                    return True
            for sel in ["#mediaplayer", ".jwplayer", "video", ".embed-responsive iframe"]:
                el = await self._page.query_selector(sel)
                if el:
                    return False
            return True
        except Exception:
            pass
        return False

    async def _wait_for_login(self, broadcast_fn=None, timeout=300):
        logger.info("Login required — waiting for user to log in via browser window")
        if broadcast_fn:
            await broadcast_fn({
                "type": "status",
                "status": "capturing",
                "message": "Login required. Please log in via the browser window that opened.",
            })

        elapsed = 0
        while elapsed < timeout:
            needs_login = await self._is_login_required()
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

    async def _detect_parts(self):
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
        if not src:
            return False
        skip_domains = [
            "twitter.com", "platform.twitter.com", "x.com",
            "facebook.com", "instagram.com", "tiktok.com",
            "google.com/recaptcha", "doubleclick.net", "googlesyndication.com",
        ]
        return any(domain in src for domain in skip_domains)

    async def _find_video_element(self) -> bool:
        # Strategy 1: JWPlayer or video on main page
        try:
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

        # Strategy 2: Embed iframes (skip social)
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

        # Strategy 3: Remaining iframes
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

    def get_match_data(self) -> dict:
        """Return scraped match page data."""
        return self.match_data

    async def _is_video_paused(self) -> bool:
        try:
            return bool(await self._video_element.evaluate("el => el.paused"))
        except Exception:
            return True
