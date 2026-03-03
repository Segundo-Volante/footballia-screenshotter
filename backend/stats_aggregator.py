"""
Statistics Aggregator — computes season-level aggregate statistics.

Queries the SQLite database across all captures for a project and produces:
- Total matches captured vs total in library
- Total frames, API cost, capture time
- Camera angle distribution (bar chart data)
- Per-match capture rates
- Classification accuracy (from reviewed frames)
- Average stats per match
"""
import json
import logging
from pathlib import Path
from typing import Optional

from backend.match_db import MatchDB

logger = logging.getLogger(__name__)


class StatsAggregator:

    def __init__(self, db: Optional[MatchDB] = None):
        self._db = db or MatchDB()

    def get_season_stats(self) -> dict:
        """Compute aggregate statistics across all matches and captures."""
        conn = self._db.conn

        # ── Match-level stats ──
        total_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        captured_matches = conn.execute(
            "SELECT COUNT(DISTINCT match_id) FROM captures WHERE match_id IS NOT NULL"
        ).fetchone()[0]
        matches_with_url = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE footballia_url != '' AND footballia_url IS NOT NULL"
        ).fetchone()[0]

        # ── Frame-level stats ──
        frame_stats = conn.execute("""
            SELECT
                COUNT(*) as total_frames,
                SUM(CASE WHEN is_reviewed = 1 THEN 1 ELSE 0 END) as reviewed_frames,
                SUM(CASE WHEN is_reviewed = 1 AND reviewed_type != camera_type THEN 1 ELSE 0 END) as corrected_frames,
                AVG(confidence) as avg_confidence
            FROM frames
        """).fetchone()

        total_frames = frame_stats[0] or 0
        reviewed_frames = frame_stats[1] or 0
        corrected_frames = frame_stats[2] or 0
        avg_confidence = round(frame_stats[3] or 0, 3)

        # Classification accuracy
        accuracy = 0.0
        if reviewed_frames > 0:
            accuracy = round((reviewed_frames - corrected_frames) / reviewed_frames * 100, 1)

        # ── Camera angle distribution ──
        distribution = {}
        rows = conn.execute(
            "SELECT camera_type, COUNT(*) as cnt FROM frames GROUP BY camera_type ORDER BY cnt DESC"
        ).fetchall()
        for row in rows:
            distribution[row[0]] = row[1]

        # ── Capture-level stats ──
        capture_stats = conn.execute("""
            SELECT
                COUNT(*) as total_captures,
                SUM(CASE WHEN provider IS NOT NULL THEN 1 ELSE 0 END) as with_provider,
                GROUP_CONCAT(DISTINCT provider) as providers_used
            FROM captures
        """).fetchone()

        total_captures = capture_stats[0] or 0

        # ── Cost stats (from frames with cost data) ──
        # Cost is tracked per-capture in the capture record or per-frame
        # For now, estimate from frame counts and known per-frame costs
        cost_rows = conn.execute("""
            SELECT c.provider, COUNT(f.id) as frame_count
            FROM captures c
            LEFT JOIN frames f ON f.capture_id = c.id
            GROUP BY c.provider
        """).fetchall()

        cost_per_provider = {
            "openai": 0.00007,
            "gemini": 0.00004,
            "manual": 0.0,
        }
        estimated_cost = sum(
            row[1] * cost_per_provider.get(row[0] or "openai", 0.00007)
            for row in cost_rows
        )

        # ── Per-match breakdown ──
        per_match = []
        match_rows = conn.execute("""
            SELECT
                m.id, m.match_day, m.opponent, m.home_away, m.date, m.score,
                COUNT(f.id) as frame_count,
                GROUP_CONCAT(DISTINCT c.provider) as provider
            FROM matches m
            LEFT JOIN captures c ON c.match_id = m.id
            LEFT JOIN frames f ON f.capture_id = c.id
            GROUP BY m.id
            ORDER BY m.match_day, m.date
        """).fetchall()

        for row in match_rows:
            per_match.append({
                "id": row[0],
                "match_day": row[1],
                "opponent": row[2],
                "home_away": row[3],
                "date": row[4],
                "score": row[5],
                "frame_count": row[6] or 0,
                "provider": row[7] or "",
                "captured": (row[6] or 0) > 0,
            })

        # ── Averages ──
        avg_frames = round(total_frames / captured_matches, 1) if captured_matches > 0 else 0
        avg_cost = round(estimated_cost / captured_matches, 4) if captured_matches > 0 else 0

        return {
            "matches": {
                "total": total_matches,
                "captured": captured_matches,
                "with_url": matches_with_url,
                "capture_pct": round(captured_matches / total_matches * 100, 1) if total_matches > 0 else 0,
            },
            "frames": {
                "total": total_frames,
                "reviewed": reviewed_frames,
                "corrected": corrected_frames,
                "avg_confidence": avg_confidence,
                "accuracy_pct": accuracy,
            },
            "cost": {
                "estimated_total": round(estimated_cost, 4),
                "avg_per_match": avg_cost,
            },
            "captures": {
                "total": total_captures,
            },
            "distribution": distribution,
            "per_match": per_match,
            "averages": {
                "frames_per_match": avg_frames,
                "cost_per_match": avg_cost,
            },
        }

    def get_correction_feedback(self) -> Optional[dict]:
        """
        Scan all annotation_ready/ folders for corrections.json files.
        Returns aggregate feedback if corrections exist.
        """
        import glob

        corrections_files = glob.glob("recordings/*/annotation_ready/corrections.json")
        if not corrections_files:
            return None

        total_frames = 0
        total_corrections = 0
        correction_patterns = {}  # {(original, corrected): count}

        for path in corrections_files:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                for fname, info in data.items():
                    total_frames += 1
                    original = info.get("original_camera_type", "")
                    corrected = info.get("corrected_shot_type", "")
                    if original and corrected and original != corrected:
                        total_corrections += 1
                        key = (original, corrected)
                        correction_patterns[key] = correction_patterns.get(key, 0) + 1
            except Exception:
                continue

        if total_frames == 0:
            return None

        correction_rate = round(total_corrections / total_frames * 100, 1)

        # Find the most common correction pattern
        top_patterns = sorted(correction_patterns.items(), key=lambda x: -x[1])[:5]

        return {
            "total_frames_reviewed": total_frames,
            "total_corrections": total_corrections,
            "correction_rate_pct": correction_rate,
            "top_patterns": [
                {"from": k[0], "to": k[1], "count": v}
                for k, v in top_patterns
            ],
            "recommendation": (
                f"Classification accuracy is {100 - correction_rate:.0f}% "
                f"(based on {total_frames} reviewed frames in Annotation Tool). "
                + (
                    f"The most common error is {top_patterns[0][0][0]} being corrected "
                    f"to {top_patterns[0][0][1]} ({top_patterns[0][1]} times). "
                    f"Consider reviewing the prompt definition for {top_patterns[0][0][0]}."
                    if top_patterns else ""
                )
            ) if correction_rate > 15 else None,
        }

    def close(self):
        self._db.close()
