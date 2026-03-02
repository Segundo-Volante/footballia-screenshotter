"""
Footballia Page Scraper — extracts structured match data from loaded Footballia pages.

Works by parsing the DOM of a page already open in Playwright.
Zero additional network requests (the page is already loaded for video playback).

Extracts:
- Team names
- Competition, season, stage, venue, date
- Full lineups (number, name, age) for both teams
- Coach info
- Match result and goal scorers with minutes (after clicking "Show result")
- Rating and vote count
"""
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class FootballiaScraper:
    """
    Extracts match data from a Footballia match page.

    Usage:
        scraper = FootballiaScraper()
        data = await scraper.scrape_match_page(page)
        # data = {home_team, away_team, home_lineup, goals, ...}
    """

    async def scrape_match_page(self, page) -> dict:
        """
        Extract all available structured data from the current Footballia match page.

        Args:
            page: Playwright page object already navigated to a match URL
                  (e.g. https://footballia.eu/matches/juventus-fc-celtic-fc)

        Returns:
            dict with all scraped fields. Missing data has empty defaults.
        """
        data = {
            "home_team": "",
            "away_team": "",
            "competition": "",
            "season": "",
            "stage": "",
            "venue": "",
            "date": "",
            "home_lineup": [],
            "away_lineup": [],
            "home_coach": None,
            "away_coach": None,
            "result": None,
            "goals": [],
            "rating": 0.0,
            "votes": 0,
            "scrape_success": False,
        }

        try:
            # ── Team names ──
            # Footballia shows team names below the crests.
            # The page title format is: "Team A vs. Team B YYYY-YYYY | Footballia"
            title = await page.title()
            if " vs. " in title:
                match_part = title.split("|")[0].strip()
                parts = match_part.split(" vs. ")
                if len(parts) == 2:
                    data["home_team"] = parts[0].strip()
                    # Remove season suffix from away team
                    away = parts[1].strip()
                    away = re.sub(r"\s+\d{4}-\d{4}$", "", away).strip()
                    data["away_team"] = away

            # ── Competition, season, stage, venue, date ──
            info_section = await page.query_selector(".match-info, .info-section")
            if info_section:
                info_text = await info_section.inner_text()
                lines = [l.strip() for l in info_text.split("\n") if l.strip()]
                for i, line in enumerate(lines):
                    if "Cup" in line or "Liga" in line or "League" in line or "Serie" in line or "Division" in line or "Championship" in line or "Friendly" in line:
                        comp_match = re.match(r"^(.+?)(\d{4}-\d{4})?$", line)
                        if comp_match:
                            data["competition"] = comp_match.group(1).strip()
                            if comp_match.group(2):
                                data["season"] = comp_match.group(2).strip()
                    if re.match(r"^(Round|Quarter|Semi|Final|Group|Play)", line):
                        data["stage"] = line
                    # Match Footballia venue format: has parentheses for city
                    if "(" in line and ")" in line and not re.search(r"\d{4}", line):
                        data["venue"] = line
                    # Date format: Month DD, YYYY
                    date_match = re.match(r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}$", line)
                    if date_match:
                        data["date"] = line

            # If we didn't get season from info, try page title
            if not data["season"]:
                season_match = re.search(r"(\d{4}-\d{4})", title)
                if season_match:
                    data["season"] = season_match.group(1)

            # ── Lineups ──
            data["home_lineup"] = await self._extract_lineup(page, "home")
            data["away_lineup"] = await self._extract_lineup(page, "away")

            # ── Coaches ──
            data["home_coach"] = await self._extract_coach(page, "home")
            data["away_coach"] = await self._extract_coach(page, "away")

            # ── Result and Goals (requires clicking "Show result") ──
            await self._reveal_result(page)
            result_data = await self._extract_result(page)
            if result_data:
                data["result"] = result_data.get("score")
                data["goals"] = result_data.get("goals", [])

            # ── Rating ──
            try:
                rating_el = await page.query_selector(".stars, .rating")
                if rating_el:
                    rating_text = await rating_el.inner_text()
                    stars = rating_text.count("\u2605") + rating_text.count("\u2b50")
                    if stars > 0:
                        data["rating"] = float(stars)
                votes_el = await page.query_selector(".votes, .vote-count")
                if votes_el:
                    votes_text = await votes_el.inner_text()
                    nums = re.findall(r"\d+", votes_text)
                    if nums:
                        data["votes"] = int(nums[0])
            except Exception:
                pass

            data["scrape_success"] = True
            logger.info(f"Scraped match data: {data['home_team']} vs {data['away_team']}, "
                        f"{len(data['home_lineup'])} + {len(data['away_lineup'])} players")

        except Exception as e:
            logger.error(f"Scraper error: {e}")
            data["scrape_success"] = False

        return data

    async def _extract_lineup(self, page, side: str) -> list[dict]:
        """
        Extract player lineup for one team.

        Returns: [{number: 1, name: "Zoff", age: 39, url: "/players/dino-zoff"}, ...]
        """
        players = []
        try:
            # Strategy: find all player links on the page, then split into home/away
            all_player_links = await page.query_selector_all('a[href*="/players/"]')

            # Collect all player rows with their positions for left/right split
            player_elements = []
            for link in all_player_links:
                parent = await link.evaluate_handle("el => el.closest('tr') || el.parentElement")
                if not parent:
                    continue
                bbox = await parent.bounding_box()
                if not bbox:
                    continue

                name = (await link.inner_text()).strip()
                url = await link.get_attribute("href")
                if not name or "/players/" not in (url or ""):
                    continue

                # Get the full row text for number and age extraction
                row_text = await parent.inner_text()
                player_elements.append({
                    "name": name,
                    "url": url or "",
                    "row_text": row_text,
                    "x": bbox["x"],
                    "width": bbox["width"],
                })

            if not player_elements:
                return []

            # Split into left (home) and right (away) by x-coordinate
            page_width = await page.evaluate("window.innerWidth")
            midpoint = page_width / 2

            left_players = [p for p in player_elements if p["x"] + p["width"] / 2 < midpoint]
            right_players = [p for p in player_elements if p["x"] + p["width"] / 2 >= midpoint]

            target = left_players if side == "home" else right_players

            for p in target:
                row = p["row_text"]
                # Extract number: first standalone integer in the row
                nums = re.findall(r"\b(\d{1,2})\b", row)
                number = int(nums[0]) if nums else 0
                # Extract age: last standalone integer (typically 2 digits, 16-45)
                age = 0
                if len(nums) >= 2:
                    candidate = int(nums[-1])
                    if 15 <= candidate <= 50:
                        age = candidate

                # Filter out coach entries (handled separately)
                if "Coach" in row or "coach" in row:
                    continue

                players.append({
                    "number": number,
                    "name": p["name"],
                    "age": age,
                    "url": p["url"],
                })

        except Exception as e:
            logger.warning(f"Lineup extraction error ({side}): {e}")

        return players

    async def _extract_coach(self, page, side: str) -> Optional[dict]:
        """Extract coach information for one team."""
        try:
            coach_elements = await page.query_selector_all('text=Coach')
            if not coach_elements:
                return None

            page_width = await page.evaluate("window.innerWidth")
            midpoint = page_width / 2

            for el in coach_elements:
                bbox = await el.bounding_box()
                if not bbox:
                    continue

                is_left = bbox["x"] + bbox["width"] / 2 < midpoint
                if (side == "home" and is_left) or (side == "away" and not is_left):
                    # Find the coach name link near this "Coach" label
                    parent = await el.evaluate_handle("el => el.closest('tr') || el.parentElement?.parentElement")
                    if parent:
                        link = await parent.query_selector('a[href*="/players/"]')
                        if link:
                            name = (await link.inner_text()).strip()
                            url = await link.get_attribute("href")
                            row_text = await parent.inner_text()
                            nums = re.findall(r"\b(\d{2})\b", row_text)
                            age = int(nums[-1]) if nums and 25 <= int(nums[-1]) <= 90 else 0
                            return {"name": name, "age": age, "url": url or ""}

        except Exception as e:
            logger.warning(f"Coach extraction error ({side}): {e}")

        return None

    async def _reveal_result(self, page):
        """Click the 'Show result' link to reveal score and goal scorers."""
        try:
            show_result = await page.query_selector('a:has-text("Show result"), text=Show result')
            if show_result:
                await show_result.click()
                await page.wait_for_timeout(500)
        except Exception as e:
            logger.debug(f"Show result click: {e}")

    async def _extract_result(self, page) -> Optional[dict]:
        """
        Extract match score and goal details after 'Show result' has been clicked.

        Returns: {"score": {"home": 2, "away": 0}, "goals": [{minute: 23, scorer: "Brady", team: "home"}, ...]}
        """
        try:
            result_section = await page.query_selector(".result, .score, .match-result")

            # Broader approach: look for "X - Y" pattern in the visible text
            body_text = await page.inner_text("body")
            score_match = re.search(r"\b(\d{1,2})\s*[-\u2013]\s*(\d{1,2})\b", body_text)

            if not score_match:
                return None

            home_goals = int(score_match.group(1))
            away_goals = int(score_match.group(2))

            # Extract individual goals: "NN' PlayerName" pattern
            goals = []
            goal_pattern = re.findall(r"(\d{1,3})['\u2032\u2019]\s+([A-Za-z\u00c0-\u00ff\s\-\.]+)", body_text)
            for minute_str, scorer in goal_pattern:
                minute = int(minute_str)
                scorer = scorer.strip()
                if not scorer or len(scorer) < 2:
                    continue
                goals.append({
                    "minute": minute,
                    "scorer": scorer,
                    "team": "unknown",
                })

            return {
                "score": {"home": home_goals, "away": away_goals},
                "goals": goals,
            }

        except Exception as e:
            logger.warning(f"Result extraction error: {e}")
            return None

    def resolve_goal_teams(self, match_data: dict):
        """
        Cross-reference goal scorers with lineups to determine which team scored.
        Call this after both lineups and goals are extracted.

        Modifies match_data["goals"] in place.
        """
        home_names = {p["name"].lower() for p in match_data.get("home_lineup", [])}
        away_names = {p["name"].lower() for p in match_data.get("away_lineup", [])}

        for goal in match_data.get("goals", []):
            scorer_lower = goal["scorer"].lower()
            home_match = any(scorer_lower in name or name in scorer_lower for name in home_names)
            away_match = any(scorer_lower in name or name in scorer_lower for name in away_names)

            if home_match and not away_match:
                goal["team"] = "home"
            elif away_match and not home_match:
                goal["team"] = "away"
            else:
                goal["team"] = "unknown"
