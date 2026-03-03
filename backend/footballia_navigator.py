"""
Footballia Coach/Player Navigator — scrapes match lists from person pages.

When a user enters a Footballia coach or player URL, this module:
1. Opens the page in the existing Playwright browser
2. Extracts all matches listed (grouped by club and season)
3. Returns structured data for the UI to display as a filterable tree

The user can then select matches and either:
- Add them to the Match Library for individual capture
- Start a Batch Capture of all selected matches

This is the most impactful UX improvement in the tool:
- Without it: user manually searches Footballia for each match URL (30+ min for a season)
- With it: user pastes one coach URL, selects matches, batch captures (30 seconds)
"""
import logging
import re
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class FootballiaNavigator:
    """
    Scrapes coach/player pages on Footballia to discover matches.

    Usage:
        nav = FootballiaNavigator()
        data = await nav.scrape_person_page(page, url)
        # data = {name, type, total_matches, clubs: [{name, seasons: [{season, matches: [...]}]}]}
    """

    async def scrape_person_page(self, page, url: str,
                                  broadcast_fn: Optional[Callable] = None) -> dict:
        """
        Navigate to a Footballia person page and extract all matches.

        Args:
            page: Playwright page object (reuses the existing browser session)
            url: Footballia URL — e.g. https://footballia.eu/players/giovanni-trapattoni
            broadcast_fn: Optional callback for progress updates

        Returns:
            {
                "name": "Giovanni Trapattoni",
                "type": "coach",  # or "player" or "both"
                "total_matches": 288,
                "clubs": [
                    {
                        "name": "Juventus FC",
                        "role": "coach",
                        "match_count": 98,
                        "seasons": [
                            {
                                "season": "1981-1982",
                                "competition": "European Cup",
                                "matches": [
                                    {
                                        "date": "September 30, 1981",
                                        "home_team": "Juventus FC",
                                        "away_team": "Celtic FC",
                                        "stage": "R32 2nd leg",
                                        "match_url": "/matches/juventus-fc-celtic-fc",
                                        "has_video": true
                                    },
                                    ...
                                ]
                            }
                        ]
                    }
                ]
            }
        """
        result = {
            "name": "",
            "type": "unknown",
            "total_matches": 0,
            "clubs": [],
            "scrape_success": False,
        }

        try:
            if broadcast_fn:
                await broadcast_fn({"type": "status", "message": "Loading person page..."})

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1000)

            # ── Extract person name ──
            title = await page.title()
            # Title format: "Giovanni Trapattoni | Footballia"
            result["name"] = title.split("|")[0].strip() if "|" in title else title.strip()

            # ── Detect role (coach / player / both) ──
            body_text = await page.inner_text("body")
            has_coach = "As coach" in body_text or "as coach" in body_text
            has_player = "As player" in body_text or "as player" in body_text
            if has_coach and has_player:
                result["type"] = "both"
            elif has_coach:
                result["type"] = "coach"
            elif has_player:
                result["type"] = "player"

            if broadcast_fn:
                await broadcast_fn({
                    "type": "status",
                    "message": f"Found: {result['name']} ({result['type']}). Extracting matches...",
                })

            # ── Extract match listings ──
            # Footballia person pages show matches grouped by club.
            # Each club section has a header (club name) followed by match rows.
            # Match rows are links to /matches/... pages.

            # Strategy: find all match links, then determine their club/season context
            # by looking at the section headers above them.
            all_match_links = await page.query_selector_all('a[href*="/matches/"]')

            current_club = {"name": "Unknown", "role": "unknown", "match_count": 0, "seasons": []}
            current_season = {"season": "", "competition": "", "matches": []}
            clubs = []

            # We process the page top-to-bottom. When we encounter a club header,
            # we start a new club group. When we encounter a season/competition header,
            # we start a new season group.
            # This requires walking the DOM structure.

            # Alternative approach: get all text content in order and parse the structure
            sections = await page.query_selector_all("h2, h3, h4, table, .matches-list, .club-section")

            if not sections:
                # Fallback: parse all match links with their surrounding text
                for link in all_match_links:
                    href = await link.get_attribute("href")
                    text = (await link.inner_text()).strip()
                    if not href or "/matches/" not in href:
                        continue

                    # Try to get surrounding context
                    parent = await link.evaluate_handle("el => el.closest('tr') || el.parentElement")
                    row_text = ""
                    if parent:
                        try:
                            row_text = await parent.inner_text()
                        except Exception:
                            pass

                    match_entry = self._parse_match_row(text, row_text, href)
                    if match_entry:
                        current_season["matches"].append(match_entry)

            else:
                # Walk sections to build the tree
                for section in sections:
                    tag = await section.evaluate("el => el.tagName.toLowerCase()")
                    text = (await section.inner_text()).strip()

                    if tag in ("h2", "h3"):
                        # Could be a club name or "As coach" / "As player" header
                        if "As coach" in text or "As player" in text:
                            # Role header — save current club
                            if current_season["matches"]:
                                current_club["seasons"].append(dict(current_season))
                                current_season = {"season": "", "competition": "", "matches": []}
                            if current_club["seasons"]:
                                clubs.append(dict(current_club))
                            current_club = {"name": "", "role": "coach" if "coach" in text.lower() else "player",
                                           "match_count": 0, "seasons": []}
                        else:
                            # Club name
                            if current_season["matches"]:
                                current_club["seasons"].append(dict(current_season))
                                current_season = {"season": "", "competition": "", "matches": []}
                            if current_club["name"] and current_club["seasons"]:
                                clubs.append(dict(current_club))
                            # Extract match count if present: "Juventus FC (98)"
                            count_match = re.search(r"\((\d+)\)", text)
                            count = int(count_match.group(1)) if count_match else 0
                            name = re.sub(r"\s*\(\d+\)\s*$", "", text).strip()
                            current_club = {"name": name, "role": current_club.get("role", "unknown"),
                                           "match_count": count, "seasons": []}

                    elif tag == "h4":
                        # Season/competition header: "1981-1982 European Cup"
                        if current_season["matches"]:
                            current_club["seasons"].append(dict(current_season))
                        season_match = re.match(r"(\d{4}-\d{4})\s*(.*)", text)
                        if season_match:
                            current_season = {
                                "season": season_match.group(1),
                                "competition": season_match.group(2).strip(),
                                "matches": [],
                            }
                        else:
                            current_season = {"season": text, "competition": "", "matches": []}

                    elif tag == "table" or "matches" in (await section.get_attribute("class") or ""):
                        # Match table — extract rows
                        links = await section.query_selector_all('a[href*="/matches/"]')
                        for link in links:
                            href = await link.get_attribute("href")
                            link_text = (await link.inner_text()).strip()
                            parent = await link.evaluate_handle("el => el.closest('tr') || el.parentElement")
                            row_text = ""
                            if parent:
                                try:
                                    row_text = await parent.inner_text()
                                except Exception:
                                    pass
                            match_entry = self._parse_match_row(link_text, row_text, href)
                            if match_entry:
                                current_season["matches"].append(match_entry)

            # Finalize last entries
            if current_season["matches"]:
                current_club["seasons"].append(dict(current_season))
            if current_club.get("name") or current_club.get("seasons"):
                clubs.append(dict(current_club))

            result["clubs"] = clubs
            result["total_matches"] = sum(
                len(m) for c in clubs for s in c.get("seasons", []) for m in [s.get("matches", [])]
            )
            result["scrape_success"] = True

            if broadcast_fn:
                club_count = len(clubs)
                await broadcast_fn({
                    "type": "status",
                    "message": f"Found {result['total_matches']} matches across {club_count} clubs",
                })

        except Exception as e:
            logger.error(f"Navigator error: {e}")
            result["scrape_success"] = False

        return result

    def _parse_match_row(self, link_text: str, row_text: str, href: str) -> Optional[dict]:
        """Parse a single match row into structured data."""
        if not href or "/matches/" not in href:
            return None

        # Try to extract teams from the href: /matches/team-a-team-b
        match_slug = href.split("/matches/")[-1] if "/matches/" in href else ""

        # Try to extract date from row text
        date = ""
        date_match = re.search(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2},?\s*\d{4}",
            row_text,
        )
        if date_match:
            date = date_match.group(0)

        # Extract stage info
        stage = ""
        stage_patterns = [
            r"(R\d+\s*\d*\w*\s*leg)",
            r"(Round\s+of\s+\d+)",
            r"(Quarter|Semi|Final)",
            r"(Group\s+[A-H])",
            r"(Matchday\s+\d+)",
            r"(MD\d+)",
        ]
        for pat in stage_patterns:
            m = re.search(pat, row_text, re.IGNORECASE)
            if m:
                stage = m.group(0)
                break

        # Determine teams from link text or row text
        home_team = ""
        away_team = ""
        # Link text is often "TeamA - TeamB" or just one team name
        if " - " in link_text:
            parts = link_text.split(" - ")
            home_team = parts[0].strip()
            away_team = parts[1].strip() if len(parts) > 1 else ""
        elif " vs " in link_text.lower():
            parts = re.split(r"\s+vs\.?\s+", link_text, flags=re.IGNORECASE)
            home_team = parts[0].strip()
            away_team = parts[1].strip() if len(parts) > 1 else ""
        else:
            home_team = link_text.strip()

        return {
            "date": date,
            "home_team": home_team,
            "away_team": away_team,
            "stage": stage,
            "match_url": href if href.startswith("/") else f"/matches/{match_slug}",
            "full_url": f"https://footballia.eu{href}" if href.startswith("/") else href,
            "has_video": True,  # It's on Footballia, so yes
        }

    async def scrape_team_page(self, page, url: str,
                               broadcast_fn: Optional[Callable] = None) -> dict:
        """
        Navigate to a Footballia team page and extract all matches.

        Args:
            page: Playwright page object
            url: Footballia URL — e.g. https://footballia.eu/teams/atletico-de-madrid

        Returns:
            {
                "name": "Atlético de Madrid",
                "type": "team",
                "total_matches": 523,
                "seasons": [
                    {
                        "season": "2024-2025",
                        "competitions": [
                            {
                                "name": "La Liga",
                                "matches": [
                                    {
                                        "date": "August 19, 2024",
                                        "home_team": "Villarreal CF",
                                        "away_team": "Atlético de Madrid",
                                        "score": "2-2",
                                        "match_url": "/matches/villarreal-cf-atletico-de-madrid-2024-08-19",
                                        "full_url": "https://footballia.eu/matches/...",
                                        "has_video": true,
                                        "home_away": "A"
                                    },
                                    ...
                                ]
                            }
                        ]
                    }
                ],
                "scrape_success": true
            }
        """
        result = {
            "name": "",
            "type": "team",
            "total_matches": 0,
            "seasons": [],
            "scrape_success": False,
        }

        try:
            if broadcast_fn:
                await broadcast_fn({"type": "status", "message": "Loading team page..."})

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1000)

            # ── Extract team name ──
            title = await page.title()
            result["name"] = title.split("|")[0].strip() if "|" in title else title.strip()

            if broadcast_fn:
                await broadcast_fn({
                    "type": "status",
                    "message": f"Found: {result['name']}. Extracting match listings...",
                })

            # ── Team pages structure ──
            # Footballia team pages show matches grouped by season, then competition.
            # The structure varies, but typically:
            #   <h3>2024-2025</h3>
            #   <h4>La Liga</h4>
            #   <table> match rows </table>
            #   <h4>Champions League</h4>
            #   <table> match rows </table>
            #   <h3>2023-2024</h3>
            #   ...

            current_season = ""
            current_competition = ""
            seasons: dict[str, dict[str, list]] = {}

            # Walk all headings and tables in order
            elements = await page.query_selector_all("h2, h3, h4, h5, table")

            for el in elements:
                tag = await el.evaluate("el => el.tagName.toLowerCase()")
                text = (await el.inner_text()).strip()

                if tag in ("h2", "h3"):
                    # Season header — e.g. "2024-2025"
                    season_match = re.search(r"(\d{4}[-/]\d{2,4})", text)
                    if season_match:
                        current_season = season_match.group(1)
                        if current_season not in seasons:
                            seasons[current_season] = {}
                        current_competition = ""

                elif tag in ("h4", "h5"):
                    # Competition header — e.g. "La Liga", "Champions League"
                    current_competition = text.strip()
                    if current_season and current_competition:
                        if current_competition not in seasons.get(current_season, {}):
                            seasons.setdefault(current_season, {})[current_competition] = []

                elif tag == "table":
                    # Match table
                    links = await el.query_selector_all('a[href*="/matches/"]')
                    for link in links:
                        href = await link.get_attribute("href") or ""
                        link_text = (await link.inner_text()).strip()
                        parent = await link.evaluate_handle("el => el.closest('tr') || el.parentElement")
                        row_text = ""
                        if parent:
                            try:
                                row_text = await parent.inner_text()
                            except Exception:
                                pass

                        match_entry = self._parse_match_row(link_text, row_text, href)
                        if match_entry:
                            # Determine home/away relative to this team
                            team_lower = result["name"].lower()
                            if match_entry["home_team"].lower() in team_lower or \
                               team_lower in match_entry["home_team"].lower():
                                match_entry["home_away"] = "H"
                            elif match_entry["away_team"].lower() in team_lower or \
                                 team_lower in match_entry["away_team"].lower():
                                match_entry["home_away"] = "A"
                            else:
                                match_entry["home_away"] = ""

                            # Extract score from row text if present
                            score_match = re.search(r"(\d+)\s*[-–]\s*(\d+)", row_text)
                            if score_match:
                                match_entry["score"] = f"{score_match.group(1)}-{score_match.group(2)}"

                            target_key = current_season or "unknown"
                            target_comp = current_competition or "Unknown"
                            seasons.setdefault(target_key, {}).setdefault(target_comp, []).append(match_entry)

            # Convert to output format
            for season_key in sorted(seasons.keys(), reverse=True):
                comps_data = seasons[season_key]
                season_entry = {
                    "season": season_key,
                    "competitions": [],
                }
                for comp_name, matches in comps_data.items():
                    season_entry["competitions"].append({
                        "name": comp_name,
                        "matches": matches,
                    })
                    result["total_matches"] += len(matches)
                result["seasons"].append(season_entry)

            result["scrape_success"] = True

            if broadcast_fn:
                await broadcast_fn({
                    "type": "status",
                    "message": f"Found {result['total_matches']} matches across {len(result['seasons'])} seasons",
                })

        except Exception as e:
            logger.error(f"Team navigator error: {e}")
            result["scrape_success"] = False

        return result

    def filter_team_matches(self, data: dict, season: str = None,
                            competition: str = None, home_away: str = None) -> list[dict]:
        """Filter team page results. Returns flat list of matches."""
        matches = []
        for s in data.get("seasons", []):
            if season and season not in s.get("season", ""):
                continue
            for comp in s.get("competitions", []):
                if competition and competition.lower() not in comp.get("name", "").lower():
                    continue
                for m in comp.get("matches", []):
                    if home_away and m.get("home_away", "") != home_away:
                        continue
                    m_copy = dict(m)
                    m_copy["season"] = s["season"]
                    m_copy["competition"] = comp["name"]
                    matches.append(m_copy)
        return matches

    def filter_matches(self, data: dict, club: str = None, season: str = None,
                       competition: str = None, role: str = None) -> list[dict]:
        """
        Filter the scraped match tree by club, season, competition, or role.
        Returns a flat list of match entries.
        """
        matches = []
        for c in data.get("clubs", []):
            if club and club.lower() not in c.get("name", "").lower():
                continue
            if role and c.get("role") != role:
                continue
            for s in c.get("seasons", []):
                if season and season not in s.get("season", ""):
                    continue
                if competition and competition.lower() not in s.get("competition", "").lower():
                    continue
                for m in s.get("matches", []):
                    m_with_context = dict(m)
                    m_with_context["club"] = c["name"]
                    m_with_context["season"] = s["season"]
                    m_with_context["competition"] = s["competition"]
                    matches.append(m_with_context)
        return matches
