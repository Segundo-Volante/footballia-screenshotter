"""
Footballia Lineup Scraper — extracts structured lineup data from Footballia match pages.

Operates on an already-loaded Playwright page (the match page used for video capture).
Produces lineup.json with starters, substitutes, and coaches for both teams.

Footballia DOM structure (discovered via inspect):
  <tr class="starters">        ← wraps starting XI
    <td width="45%">           ← home team (left column)
      <table> <tr class="player"> ... </tr> × 11 </table>
    </td>
    <td width="10%"></td>      ← spacer
    <td width="45%">           ← away team (right column)
      <table> <tr class="player"> ... </tr> × 11 </table>
    </td>
  </tr>
  <!-- Coach rows -->
  <tr class="reserves">        ← wraps substitutes
    <td width="45%">
      <table>
        <tr class="player"><td class="name" colspan="2">Substitutes</td></tr>
        <tr class="player"> ... </tr> × N
      </table>
    </td>
    ...
  </tr>

Each player row: <tr class="player" itemprop="competitor">
  <td class="country"><div class="flag flag-XX" title="Country"></div></td>
  <td class="team_number">NN</td>
  <td class="name"><a href="/players/slug" title="Full Name"><span itemprop="name">DisplayName</span></a></td>
  <td class="age">NN</td>

Limitations (as of 2026-03):
  - No formation info on the page
  - No position info (GK, DF, MF, FW)
  - No substitution minutes or who-replaced-who
  - Only number, name, age, country flag
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


async def scrape_lineup(page, match_url: str = "") -> Optional[dict]:
    """
    Extract lineup data from a Footballia match page.

    Args:
        page: Playwright page object already navigated to a match URL.
        match_url: The URL of the match page (for metadata).

    Returns:
        dict with home_team and away_team lineup data, or None if no lineup found.
    """
    try:
        # ── Extract team names from page title ──
        title = await page.title()
        home_name = ""
        away_name = ""
        if " vs. " in title:
            match_part = title.split("|")[0].strip()
            parts = match_part.split(" vs. ")
            if len(parts) == 2:
                home_name = parts[0].strip()
                away_name = re.sub(r"\s+\d{4}-\d{4}$", "", parts[1].strip()).strip()

        # ── Extract starters ──
        starters_row = await page.query_selector("tr.starters")
        if not starters_row:
            logger.warning("No tr.starters found — page may not have lineup data")
            return None

        # Starters row has two <td> columns: home (left) and away (right)
        starter_tds = await starters_row.query_selector_all(":scope > td")
        # Filter to the two content columns (skip the spacer)
        content_tds = []
        for td in starter_tds:
            width = await td.get_attribute("width") or ""
            if "45%" in width:
                content_tds.append(td)

        if len(content_tds) < 2:
            logger.warning(f"Expected 2 lineup columns, found {len(content_tds)}")
            return None

        home_starters = await _extract_players_from_container(content_tds[0])
        away_starters = await _extract_players_from_container(content_tds[1])

        # ── Extract substitutes ──
        reserves_row = await page.query_selector("tr.reserves")
        home_subs = []
        away_subs = []
        if reserves_row:
            reserve_tds = await reserves_row.query_selector_all(":scope > td")
            reserve_content_tds = []
            for td in reserve_tds:
                width = await td.get_attribute("width") or ""
                if "45%" in width:
                    reserve_content_tds.append(td)
            if len(reserve_content_tds) >= 2:
                home_subs = await _extract_players_from_container(reserve_content_tds[0])
                away_subs = await _extract_players_from_container(reserve_content_tds[1])

        if not home_starters and not away_starters:
            logger.warning("No players extracted from lineup tables")
            return None

        # ── Build lineup.json structure ──
        # Mark starters
        for p in home_starters:
            p["role"] = "starter"
            p["appeared"] = True
        for p in away_starters:
            p["role"] = "starter"
            p["appeared"] = True

        # All subs are listed as sub_unused by default since Footballia
        # doesn't provide sub minutes or who-replaced-who info
        for p in home_subs:
            p["role"] = "sub_unused"
            p["appeared"] = False
        for p in away_subs:
            p["role"] = "sub_unused"
            p["appeared"] = False

        result = {
            "source": "footballia",
            "match_url": match_url,
            "scrape_date": datetime.now(timezone.utc).isoformat(),
            "home_team": {
                "name": home_name,
                "formation": "",  # Not available on Footballia
                "players": home_starters + home_subs,
            },
            "away_team": {
                "name": away_name,
                "formation": "",
                "players": away_starters + away_subs,
            },
        }

        logger.info(
            f"Lineup scraped: {len(home_starters)}+{len(home_subs)} home, "
            f"{len(away_starters)}+{len(away_subs)} away players"
        )
        return result

    except Exception as e:
        logger.error(f"Lineup scraping failed: {e}")
        return None


async def _extract_players_from_container(container) -> list[dict]:
    """
    Extract player data from a lineup column <td>.

    Each player row is <tr class="player"> with:
      - td.team_number → jersey number
      - td.name > a > span[itemprop="name"] → display name
      - td.name > a[title] → full name
      - td.age → age
      - td.country > div.flag[title] → nationality

    Skips rows that are headers (e.g., "Substitutes") or coach rows.
    """
    players = []
    rows = await container.query_selector_all("tr.player")

    for row in rows:
        try:
            # Check if this is a header row (e.g., "Substitutes")
            name_td = await row.query_selector("td.name")
            if name_td:
                colspan = await name_td.get_attribute("colspan")
                if colspan and int(colspan) > 1:
                    # This is a section header like "Substitutes", skip it
                    continue

            # Check if this is a coach row
            row_text = (await row.inner_text() or "").strip()
            if "Coach" in row_text or "coach" in row_text:
                continue

            # Extract jersey number
            number = 0
            number_td = await row.query_selector("td.team_number")
            if number_td:
                num_text = (await number_td.inner_text() or "").strip()
                if num_text.isdigit():
                    number = int(num_text)

            # Extract player name (display name and full name)
            name_link = await row.query_selector("td.name a")
            if not name_link:
                continue

            display_name = ""
            name_span = await name_link.query_selector("span[itemprop='name']")
            if name_span:
                display_name = (await name_span.inner_text() or "").strip()
            if not display_name:
                display_name = (await name_link.inner_text() or "").strip()

            full_name = await name_link.get_attribute("title") or display_name
            player_url = await name_link.get_attribute("href") or ""

            if not display_name:
                continue

            # Extract age
            age = 0
            age_td = await row.query_selector("td.age")
            if age_td:
                age_text = (await age_td.inner_text() or "").strip()
                if age_text.isdigit():
                    age = int(age_text)

            # Extract nationality
            nationality = ""
            flag_div = await row.query_selector("td.country div.flag")
            if flag_div:
                nationality = await flag_div.get_attribute("title") or ""

            players.append({
                "name": display_name,
                "full_name": full_name,
                "number": number,
                "age": age,
                "nationality": nationality,
                "position": "",  # Not available on Footballia
                "url": player_url,
            })

        except Exception as e:
            logger.debug(f"Error parsing player row: {e}")
            continue

    return players


def save_lineup_json(lineup_data: dict, output_dir: str | Path) -> Path:
    """Save lineup data to lineup.json in the output directory."""
    output_path = Path(output_dir) / "lineup.json"
    output_path.write_text(
        json.dumps(lineup_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info(f"Saved lineup.json to {output_path}")
    return output_path


def load_lineup_json(output_dir: str | Path) -> Optional[dict]:
    """Load lineup.json from the output directory if it exists."""
    lineup_path = Path(output_dir) / "lineup.json"
    if not lineup_path.exists():
        return None
    try:
        return json.loads(lineup_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Failed to load lineup.json: {e}")
        return None


def generate_squad_json_from_lineup(
    lineup_data: dict,
    home_away: str = "H",
) -> dict:
    """
    Convert lineup.json data into squad.json format for the annotation tool.

    Args:
        lineup_data: Parsed lineup.json dict.
        home_away: "H" or "A" — from match DB, tells us which Footballia team
                   is the team we're tracking. "H" means we're the home team
                   on Footballia, "A" means we're the away team.

    Returns:
        squad.json dict ready for the annotation tool.
    """
    if home_away == "A":
        our_team_data = lineup_data["away_team"]
        opp_team_data = lineup_data["home_team"]
    else:
        our_team_data = lineup_data["home_team"]
        opp_team_data = lineup_data["away_team"]

    squad = {
        "home_team": {
            "name": our_team_data["name"],
            "formation": our_team_data.get("formation", ""),
            "players": [],
        },
        "away_team": {
            "name": opp_team_data["name"],
            "players": [],
        },
    }

    # Add our team's players (all players, with appeared flag)
    for player in our_team_data.get("players", []):
        squad["home_team"]["players"].append({
            "number": player.get("number", 0),
            "name": player["name"],
            "position": _map_position(player.get("position", "")),
            "appeared": player.get("appeared", True),
        })

    # Add opponent players (all players, with appeared flag)
    for player in opp_team_data.get("players", []):
        squad["away_team"]["players"].append({
            "number": player.get("number", 0),
            "name": player["name"],
            "position": _map_position(player.get("position", "")),
            "appeared": player.get("appeared", True),
        })

    return squad


def _map_position(pos: str) -> str:
    """Map Footballia position labels to annotation tool standard positions."""
    if not pos:
        return ""
    mapping = {
        "GK": "GK", "Goalkeeper": "GK",
        "DF": "CB", "Defender": "CB",
        "MF": "CM", "Midfielder": "CM",
        "FW": "ST", "Forward": "ST",
        "Right Back": "RB", "Left Back": "LB",
        "Centre Back": "CB", "Central Midfielder": "CM",
        "Attacking Midfielder": "CAM", "Defensive Midfielder": "CDM",
        "Right Midfielder": "RM", "Left Midfielder": "LM",
        "Right Winger": "RW", "Left Winger": "LW",
        "Striker": "ST", "Centre Forward": "CF",
    }
    return mapping.get(pos, pos)
