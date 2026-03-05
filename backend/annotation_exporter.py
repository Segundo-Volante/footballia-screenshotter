"""Export captured frames as a ready-to-use bundle for the annotation tool."""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Awaitable

from backend.match_db import MatchDB
from backend.lineup_scraper import load_lineup_json, generate_squad_json_from_lineup
from backend.project_config import ProjectConfig
from backend.utils import logger

# Camera categories to include in annotation exports
INCLUDE_CATEGORIES = {"WIDE_CENTER", "WIDE_LEFT", "WIDE_RIGHT", "MEDIUM"}
EXCLUDE_CATEGORIES = {"CLOSEUP", "OTHER", "AERIAL", "BEHIND_GOAL", "PENDING"}


def parse_frame_filename(filename: str) -> Optional[dict]:
    """Parse metadata from a frame filename.

    Expected format: frame_{time}_{camera_angle}_conf{confidence}.jpg
    """
    pattern = r"frame_(\d+\.\d+)_(.+)_conf(\d+)\.jpg"
    match = re.match(pattern, filename)
    if not match:
        return None

    video_time = float(match.group(1))
    camera_raw = match.group(2)
    confidence = int(match.group(3)) / 100

    camera_angle = camera_raw.upper()

    if camera_raw.startswith("wide"):
        shot = "wide"
        camera = "pan" if "left" in camera_raw or "right" in camera_raw else "static"
    elif camera_raw == "medium":
        shot = "medium"
        camera = "static"
    else:
        shot = camera_raw
        camera = "static"

    return {
        "video_time": video_time,
        "camera_angle": camera_angle,
        "camera_confidence": confidence,
        "pre_filled_metadata": {
            "shot": shot,
            "camera": camera,
        },
    }


class AnnotationExporter:
    """Exports captured frames into a bundle for the annotation tool."""

    def __init__(self, match_id: int, broadcast_fn: Optional[Callable] = None):
        self.match_id = match_id
        self._broadcast = broadcast_fn
        self.db = MatchDB()
        self.pc = ProjectConfig()

    async def _broadcast_progress(self, current: int, total: int, message: str):
        if self._broadcast:
            await self._broadcast({
                "type": "export_progress",
                "current": current,
                "total": total,
                "message": message,
            })

    def export(self) -> dict:
        """Run the export synchronously. Returns result dict."""
        match = self.db.get_match(self.match_id)
        if not match:
            return {"status": "error", "message": "Match not found"}

        # Find the recording directory for this match
        recording_dir = self._find_recording_dir(match)
        if not recording_dir:
            return {"status": "error", "message": "No recordings found for this match"}

        # Collect frames from included categories
        frames_to_copy = self._collect_frames(recording_dir)
        if not frames_to_copy:
            return {
                "status": "error",
                "message": "No useful frames found (only CLOSEUP/OTHER/AERIAL/BEHIND_GOAL)",
            }

        # Create output directory
        md = match.get("match_day", 0)
        opponent = (match.get("opponent") or "Unknown").replace(" ", "_")
        bundle_name = f"annotation_bundle_MD{md:02d}_{opponent}"
        export_dir = Path("exports") / bundle_name
        frames_dir = export_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        # Copy frames
        copied = 0
        for src_path in frames_to_copy:
            dst = frames_dir / src_path.name
            shutil.copy2(str(src_path), str(dst))
            copied += 1

        # Count skipped
        skipped = self._count_excluded_frames(recording_dir)

        # Generate match.json
        match_json = self._build_match_json(match, copied, skipped,
                                            recording_dir=recording_dir)
        (export_dir / "match.json").write_text(
            json.dumps(match_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Generate frame_metadata.json (merges sequence data from capture)
        frame_metadata = self._build_frame_metadata(frames_to_copy, recording_dir)
        (export_dir / "frame_metadata.json").write_text(
            json.dumps(frame_metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Generate squad.json (auto-populated from lineup.json if available)
        squad_json, lineup_available = self._build_squad_json(match, recording_dir)
        (export_dir / "squad.json").write_text(
            json.dumps(squad_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        files_generated = ["match.json", "frame_metadata.json", "squad.json"]

        logger.info(
            f"Annotation export complete: {copied} frames to {export_dir}"
        )

        return {
            "status": "success",
            "export_path": str(export_dir),
            "frames_exported": copied,
            "frames_skipped": skipped,
            "files_generated": files_generated,
            "lineup_available": lineup_available,
        }

    async def export_async(self) -> dict:
        """Run the export with async progress broadcasting."""
        match = self.db.get_match(self.match_id)
        if not match:
            return {"status": "error", "message": "Match not found"}

        recording_dir = self._find_recording_dir(match)
        if not recording_dir:
            return {"status": "error", "message": "No recordings found for this match"}

        frames_to_copy = self._collect_frames(recording_dir)
        if not frames_to_copy:
            return {
                "status": "error",
                "message": "No useful frames found (only CLOSEUP/OTHER/AERIAL/BEHIND_GOAL)",
            }

        md = match.get("match_day", 0)
        opponent = (match.get("opponent") or "Unknown").replace(" ", "_")
        bundle_name = f"annotation_bundle_MD{md:02d}_{opponent}"
        export_dir = Path("exports") / bundle_name
        frames_dir = export_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        total = len(frames_to_copy)
        copied = 0
        for i, src_path in enumerate(frames_to_copy):
            dst = frames_dir / src_path.name
            shutil.copy2(str(src_path), str(dst))
            copied += 1
            if total > 10 and (i + 1) % 5 == 0:
                await self._broadcast_progress(
                    i + 1, total, f"Copying frame {i + 1} of {total}..."
                )

        skipped = self._count_excluded_frames(recording_dir)

        match_json = self._build_match_json(match, copied, skipped,
                                            recording_dir=recording_dir)
        (export_dir / "match.json").write_text(
            json.dumps(match_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        frame_metadata = self._build_frame_metadata(frames_to_copy, recording_dir)
        (export_dir / "frame_metadata.json").write_text(
            json.dumps(frame_metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        squad_json, lineup_available = self._build_squad_json(match, recording_dir)
        (export_dir / "squad.json").write_text(
            json.dumps(squad_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        files_generated = ["match.json", "frame_metadata.json", "squad.json"]

        if self._broadcast:
            await self._broadcast({
                "type": "export_complete",
                "export_path": str(export_dir),
                "frames_exported": copied,
                "lineup_available": lineup_available,
            })

        logger.info(f"Annotation export complete: {copied} frames to {export_dir}")

        return {
            "status": "success",
            "export_path": str(export_dir),
            "frames_exported": copied,
            "frames_skipped": skipped,
            "files_generated": files_generated,
            "lineup_available": lineup_available,
        }

    def _find_recording_dir(self, match: dict) -> Optional[Path]:
        """Find the recording directory for a match.

        Checks captures table first, then falls back to folder naming convention.
        """
        # Try captures table
        captures = self.db.conn.execute(
            "SELECT output_dir FROM captures WHERE match_id = ? "
            "AND status IN ('completed', 'interrupted') ORDER BY id DESC",
            (self.match_id,),
        ).fetchall()

        for cap in captures:
            d = cap["output_dir"]
            if d and Path(d).exists():
                return Path(d)

        # Fallback: scan recordings/ for a matching folder name
        rec_dir = Path("recordings")
        if not rec_dir.exists():
            return None
        md = match.get("match_day", 0)
        opponent = (match.get("opponent") or "").replace(" ", "_")
        prefix = f"MD{md:02d}_{opponent}"
        for child in rec_dir.iterdir():
            if child.is_dir() and child.name.startswith(prefix):
                return child
        return None

    def _collect_frames(self, recording_dir: Path) -> list[Path]:
        """Collect all frame files from included camera categories."""
        frames = []
        for cat in INCLUDE_CATEGORIES:
            cat_dir = recording_dir / cat
            if cat_dir.exists():
                for f in sorted(cat_dir.iterdir()):
                    if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                        frames.append(f)
        return frames

    def _count_excluded_frames(self, recording_dir: Path) -> int:
        """Count frames in excluded categories."""
        count = 0
        for cat in EXCLUDE_CATEGORIES:
            cat_dir = recording_dir / cat
            if cat_dir.exists():
                count += sum(
                    1
                    for f in cat_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")
                )
        return count

    def _build_match_json(self, match: dict, exported: int, skipped: int,
                          recording_dir: Optional[Path] = None) -> dict:
        competition = match.get("competition", "")
        if not competition and self.pc.exists:
            comps = self.pc.competitions
            competition = comps[0] if comps else "LaLiga"

        home_away = match.get("home_away", "H")
        team_name = self.pc.team_name if self.pc.exists else ""
        opponent = match.get("opponent", "")

        # Derive absolute home/away team names
        # On Footballia, the first team listed is always Home, second is Away.
        # Our DB stores home_away as "H" (our team is home) or "A" (our team is away).
        home_team_name = ""
        away_team_name = ""

        # Try lineup.json first (most reliable — actual Footballia order)
        if recording_dir:
            lineup_data = load_lineup_json(recording_dir)
            if lineup_data:
                home_team_name = lineup_data.get("home_team", {}).get("name", "")
                away_team_name = lineup_data.get("away_team", {}).get("name", "")

        # Fallback: derive from project team name + opponent + home_away
        if not home_team_name and (team_name or opponent):
            if home_away == "A":
                home_team_name = opponent
                away_team_name = team_name
            else:
                home_team_name = team_name
                away_team_name = opponent

        result = {
            "opponent": opponent,
            "matchday": match.get("match_day", 0),
            "competition": competition,
            "date": match.get("date", ""),
            "home_away": home_away,
            "home_team_name": home_team_name,
            "away_team_name": away_team_name,
            "source": "footballia",
            "capture_summary": {
                "total_captured": exported + skipped,
                "exported_for_annotation": exported,
                "skipped_categories": sorted(EXCLUDE_CATEGORIES - {"PENDING"}),
                "included_categories": sorted(INCLUDE_CATEGORIES),
            },
        }
        return result

    def _build_frame_metadata(self, frames: list[Path], recording_dir: Path | None = None) -> dict:
        """Build frame_metadata.json from the list of frame files.

        If a frame_metadata.json already exists in the recording directory (from
        the capture pipeline), merge it in so that sequence data, session_info, and
        per-frame sequence fields are preserved in the annotation bundle.
        """
        # Try to load existing capture-time metadata
        existing_meta: dict = {}
        existing_frames_by_name: dict[str, dict] = {}
        if recording_dir:
            src_meta_path = recording_dir / "frame_metadata.json"
            if src_meta_path.exists():
                try:
                    existing_meta = json.loads(src_meta_path.read_text(encoding="utf-8"))
                    for fm in existing_meta.get("frames", []):
                        existing_frames_by_name[fm.get("file_name", "")] = fm
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("Failed to read source frame_metadata.json: %s", e)

        # Build exported frame names set (for filtering sequence_summary)
        exported_names = {f.name for f in frames}

        # Build per-frame entries, merging capture-time data when available
        entries = []
        for f in frames:
            existing = existing_frames_by_name.get(f.name)
            if existing:
                # Use the full capture-time record (has sequence_id, etc.)
                entry = dict(existing)
                entry["file_name"] = f.name  # ensure consistency
            else:
                # Fallback: parse from filename
                parsed = parse_frame_filename(f.name)
                if parsed:
                    entry = {"file_name": f.name, **parsed}
                else:
                    entry = {
                        "file_name": f.name,
                        "video_time": 0,
                        "camera_angle": "UNKNOWN",
                        "camera_confidence": 0,
                        "pre_filled_metadata": {"shot": "wide", "camera": "static"},
                    }
            entries.append(entry)

        result: dict = {"frames": entries}

        # Carry over top-level metadata from capture
        if existing_meta.get("schema_version"):
            result["schema_version"] = existing_meta["schema_version"]
        if existing_meta.get("session_info"):
            result["session_info"] = existing_meta["session_info"]

        # Filter sequence_summary to only include sequences that have at least
        # one frame in the exported bundle
        if existing_meta.get("sequence_summary"):
            exported_seq_ids = {
                e.get("sequence_id") for e in entries if e.get("sequence_id")
            }
            result["sequence_summary"] = [
                s for s in existing_meta["sequence_summary"]
                if s.get("sequence_id") in exported_seq_ids
            ]

        return result

    def _build_squad_json(self, match: dict, recording_dir: Path) -> tuple[dict, bool]:
        """Build squad.json, auto-populating from lineup.json if available.

        Returns:
            (squad_dict, lineup_available) — the squad data and whether lineup.json was used.
        """
        home_away = match.get("home_away", "H")

        # ── Try lineup.json first ──
        lineup_data = load_lineup_json(recording_dir)
        if lineup_data:
            logger.info("Using lineup.json to populate squad.json")
            squad = generate_squad_json_from_lineup(lineup_data, home_away)
            return squad, True

        # ── Fallback: manual placeholder ──
        team_name = self.pc.team_name if self.pc.exists else ""
        opponent = match.get("opponent", "")

        if home_away == "A":
            home_name = opponent
            away_name = team_name
        else:
            home_name = team_name
            away_name = opponent

        home_players = []
        away_players = []

        # Try to parse Starting XI from match data (Excel import)
        starting_xi = match.get("starting_xi", "")
        if starting_xi:
            try:
                names = [n.strip() for n in starting_xi.split(",") if n.strip()]
                if names:
                    if home_away == "A":
                        away_players = [
                            {"number": i + 1, "name": n, "position": ""}
                            for i, n in enumerate(names)
                        ]
                    else:
                        home_players = [
                            {"number": i + 1, "name": n, "position": ""}
                            for i, n in enumerate(names)
                        ]
            except Exception:
                pass

        squad = {
            "home_team": {
                "name": home_name,
                "formation": "",
                "players": home_players,
            },
            "away_team": {
                "name": away_name,
                "players": away_players,
            },
            "_instructions": (
                "Fill in team names, formation, and player lists before giving "
                "this bundle to annotators. Each player needs: number (int), "
                "name (string), position (string: GK/RB/CB/LB/CDM/CM/CAM/RM/LM/"
                "RW/LW/ST/CF). See the annotation tool README for details."
            ),
        }
        return squad, False

    def close(self):
        self.db.close()
