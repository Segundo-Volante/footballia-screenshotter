"""
Annotation Bridge — generates the annotation_ready/ output package.

After a capture completes, this module creates:

recordings/{match_folder}/annotation_ready/
├── images/                          # Flat folder of all captured frames (symlinks or copies)
├── screenshotter_metadata.json      # Full metadata for every frame + match info
├── annotation_bridge.json           # Mapping: screenshotter camera_type → annotation tool fields
└── rosters/                         # If lineups were scraped from Footballia
    ├── home_{team_name}.csv
    └── away_{team_name}.csv

The football-annotation-tool detects this structure when user selects the images/ folder:
1. Reads screenshotter_metadata.json → auto-fills session dialog (competition, round, opponent)
2. Reads annotation_bridge.json → pre-fills shot_type and camera_motion per frame
3. Reads rosters/ → enables player name autocomplete for bounding box annotations
4. Frames tagged as OTHER/is_replay → auto-skip in annotation (saves time)
"""
import json
import logging
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# Default mapping from screenshotter camera types to annotation tool fields.
# The annotation tool uses "shot_type" and "camera_motion" dropdowns.
DEFAULT_BRIDGE_MAPPING = {
    "WIDE_CENTER": {"shot_type": "wide", "camera_motion": "static"},
    "WIDE_LEFT": {"shot_type": "wide", "camera_motion": "pan"},
    "WIDE_RIGHT": {"shot_type": "wide", "camera_motion": "pan"},
    "MEDIUM": {"shot_type": "medium", "camera_motion": "static"},
    "CLOSEUP": {"shot_type": "close-up", "camera_motion": "static"},
    "BEHIND_GOAL": {"shot_type": "behind-goal", "camera_motion": "static"},
    "AERIAL": {"shot_type": "aerial", "camera_motion": "static"},
    "OTHER": {"shot_type": "other", "camera_motion": "unknown", "auto_skip": True},
    "PENDING": {"shot_type": "", "camera_motion": "", "auto_skip": False},
}


class AnnotationBridge:
    """
    Generates the annotation_ready/ package for a completed capture.
    """

    def __init__(self, output_dir: str, match_data: dict, capture_data: dict,
                 bridge_mapping: dict = None):
        """
        Args:
            output_dir: Path to the capture's output directory (e.g. recordings/MD05_Valencia_H/)
            match_data: Match metadata (from database or scraped data)
            capture_data: Capture session data (provider, task, stats, etc.)
            bridge_mapping: Custom camera_type → annotation_tool field mapping
        """
        self._output_dir = Path(output_dir)
        self._match_data = match_data
        self._capture_data = capture_data
        self._mapping = bridge_mapping or DEFAULT_BRIDGE_MAPPING
        self._ready_dir = self._output_dir / "annotation_ready"

    def generate(self, frames: list[dict], scraped_data: dict = None):
        """
        Generate the complete annotation_ready/ package.

        Args:
            frames: List of frame records from the database (id, filename, filepath, etc.)
            scraped_data: Optional scraped Footballia data (lineups, goals, etc.)
        """
        self._ready_dir.mkdir(parents=True, exist_ok=True)
        images_dir = self._ready_dir / "images"
        images_dir.mkdir(exist_ok=True)

        # ── 1. Copy/link frames into flat images/ directory ──
        frame_map = {}
        for f in frames:
            src = Path(f["filepath"])
            if not src.exists():
                continue
            dest = images_dir / src.name
            if not dest.exists():
                # Prefer symlink (saves disk space), fall back to copy
                try:
                    dest.symlink_to(src.resolve())
                except (OSError, NotImplementedError):
                    shutil.copy2(src, dest)
            frame_map[src.name] = f

        # ── 2. Generate screenshotter_metadata.json ──
        metadata = self._build_metadata(frames, scraped_data)
        meta_path = self._ready_dir / "screenshotter_metadata.json"
        meta_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # ── 3. Write annotation_bridge.json ──
        bridge_path = self._ready_dir / "annotation_bridge.json"
        bridge_content = {
            "version": "1.0.0",
            "description": "Maps screenshotter classifications to annotation tool fields",
            "mapping": self._mapping,
        }
        bridge_path.write_text(
            json.dumps(bridge_content, indent=2) + "\n",
            encoding="utf-8",
        )

        # ── 4. Generate roster CSVs (if lineups available) ──
        if scraped_data:
            self._generate_rosters(scraped_data)

        logger.info(
            f"annotation_ready/ generated: {len(frame_map)} images, "
            f"metadata.json, bridge.json"
            f"{', rosters' if scraped_data and scraped_data.get('home_lineup') else ''}"
        )

        return str(self._ready_dir)

    def _build_metadata(self, frames: list[dict], scraped_data: dict = None) -> dict:
        """Build the screenshotter_metadata.json structure."""
        match = self._match_data
        scraped = scraped_data or {}

        # Match section
        match_meta = {
            "home_team": scraped.get("home_team", match.get("team_name", "")),
            "away_team": scraped.get("away_team", match.get("opponent", "")),
            "date": match.get("date", scraped.get("date", "")),
            "competition": match.get("competition", scraped.get("competition", "")),
            "season": match.get("season", scraped.get("season", "")),
            "matchday": match.get("match_day", 0),
            "venue": scraped.get("venue", match.get("venue", "")),
            "stage": scraped.get("stage", match.get("stage", "")),
        }

        # Add score and goals from scraped data
        if scraped.get("result"):
            match_meta["score"] = scraped["result"]
        if scraped.get("goals"):
            match_meta["goals"] = scraped["goals"]

        # Determine home/away based on match data
        if match.get("home_away") == "A":
            # Our team is away — swap display order
            match_meta["home_team"], match_meta["away_team"] = (
                match.get("opponent", ""), match.get("team_name", "")
            )

        # Capture config section
        capture_meta = {
            "provider": self._capture_data.get("provider", ""),
            "model": self._capture_data.get("model", "gpt-4o-mini"),
            "task": self._capture_data.get("task_id", "camera_angle"),
            "capture_mode": self._capture_data.get("capture_mode", "full_match"),
            "source_type": self._capture_data.get("source_type", "footballia"),
            "interval_base": self._capture_data.get("interval_base", 2.0),
            "adaptive_sampling": self._capture_data.get("adaptive", True),
            "pre_filter": self._capture_data.get("pre_filter_enabled", True),
        }

        # Statistics
        stats = {
            "total_captured": len(frames),
            "api_cost_usd": self._capture_data.get("api_cost", 0.0),
            "api_calls": self._capture_data.get("api_calls", 0),
            "duration_seconds": self._capture_data.get("duration_seconds", 0.0),
        }
        filter_stats = self._capture_data.get("filter_stats", {})
        if filter_stats:
            stats["total_frames_seen"] = filter_stats.get("total", 0)
            stats["pre_filtered"] = filter_stats.get("total", 0) - filter_stats.get("passed", 0)

        # Key moments (from goals)
        key_moments = []
        for g in scraped.get("goals", []):
            key_moments.append({
                "type": "goal",
                "time": g.get("minute", 0) * 60,
                "scorer": g.get("scorer", ""),
                "team": g.get("team", "unknown"),
            })

        # Per-frame metadata
        frames_meta = {}
        for f in frames:
            raw = {}
            if f.get("raw_response"):
                try:
                    raw = json.loads(f["raw_response"]) if isinstance(f["raw_response"], str) else f["raw_response"]
                except Exception:
                    pass

            frame_entry = {
                "video_time": f.get("video_time", 0.0),
                "video_part": f.get("video_part", 1),
                "camera_type": f.get("camera_type", ""),
                "confidence": f.get("confidence", 0.0),
                "is_replay": bool(f.get("is_replay", False)),
                "is_reviewed": bool(f.get("is_reviewed", False)),
                "was_corrected": f.get("reviewed_type", "") != "" and f.get("reviewed_type") != f.get("camera_type"),
                "players_visible": raw.get("players_visible", 0),
                "pitch_visible_pct": raw.get("pitch_visible_pct", 0),
                "reasoning": raw.get("reasoning", ""),
            }

            # Check if near a key moment
            frame_time = f.get("video_time", 0.0)
            for km in key_moments:
                if abs(frame_time - km["time"]) < 60:
                    frame_entry["near_key_moment"] = True
                    frame_entry["moment_type"] = km["type"]
                    frame_entry["moment_offset"] = round(frame_time - km["time"], 1)
                    break

            frames_meta[f.get("filename", "")] = frame_entry

        return {
            "source_tool": "footballia-screenshotter",
            "version": "2.0.0",
            "generated_at": datetime.now().isoformat(),
            "match": match_meta,
            "capture_config": capture_meta,
            "statistics": stats,
            "key_moments": key_moments,
            "frames": frames_meta,
        }

    def _generate_rosters(self, scraped_data: dict):
        """Generate roster CSV files from scraped lineup data."""
        rosters_dir = self._ready_dir / "rosters"
        rosters_dir.mkdir(exist_ok=True)

        for side in ["home", "away"]:
            lineup = scraped_data.get(f"{side}_lineup", [])
            team_name = scraped_data.get(f"{side}_team", side)
            coach = scraped_data.get(f"{side}_coach")
            season = scraped_data.get("season", "")

            if not lineup:
                continue

            # Sanitize team name for filename
            safe_name = team_name.lower().replace(" ", "_").replace(".", "")
            safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")
            csv_path = rosters_dir / f"{side}_{safe_name}.csv"

            lines = ["team,season,number,name,role"]
            for player in lineup:
                name = player.get("name", "").replace(",", "")
                number = player.get("number", 0)
                lines.append(f"{team_name},{season},{number},{name},player")

            if coach:
                coach_name = coach.get("name", "").replace(",", "")
                lines.append(f"{team_name},{season},,{coach_name},coach")

            csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            logger.info(f"Roster CSV: {csv_path.name} ({len(lineup)} players)")
