"""
SQLite database for matches and capture history.
Provides crash-resilient storage — frames are recorded as they are saved,
not at the end of a pipeline run.
"""
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

DB_PATH = Path("data/matches.db")


class MatchDB:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_day INTEGER,
                date TEXT,
                home_away TEXT,
                opponent TEXT,
                score TEXT DEFAULT '',
                result TEXT DEFAULT '',
                competition TEXT DEFAULT '',
                season TEXT DEFAULT '',
                team_name TEXT DEFAULT '',
                venue TEXT DEFAULT '',
                stage TEXT DEFAULT '',
                footballia_url TEXT DEFAULT '',
                starting_xi TEXT DEFAULT '',
                substitutes TEXT DEFAULT '',
                goal_scorers TEXT DEFAULT '',
                referee TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER REFERENCES matches(id),
                status TEXT DEFAULT 'pending',
                provider TEXT DEFAULT 'openai',
                source_type TEXT DEFAULT 'footballia',
                total_captured INTEGER DEFAULT 0,
                total_classified INTEGER DEFAULT 0,
                api_cost REAL DEFAULT 0.0,
                output_dir TEXT DEFAULT '',
                start_time TEXT DEFAULT '',
                duration_seconds REAL DEFAULT 0.0,
                config_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                capture_id INTEGER REFERENCES captures(id),
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                video_time REAL,
                video_part INTEGER DEFAULT 1,
                camera_type TEXT DEFAULT '',
                confidence REAL DEFAULT 0.0,
                players_visible INTEGER DEFAULT 0,
                pitch_visible_pct INTEGER DEFAULT 0,
                is_replay INTEGER DEFAULT 0,
                is_reviewed INTEGER DEFAULT 0,
                reviewed_type TEXT DEFAULT '',
                raw_response TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        self.conn.commit()

        # ── Migrations: add columns if missing ──
        self._migrate_add_column("matches", "home_lineup_json", "TEXT DEFAULT ''")
        self._migrate_add_column("matches", "away_lineup_json", "TEXT DEFAULT ''")
        self._migrate_add_column("matches", "home_coach_json", "TEXT DEFAULT ''")
        self._migrate_add_column("matches", "away_coach_json", "TEXT DEFAULT ''")
        self._migrate_add_column("matches", "goals_json", "TEXT DEFAULT '[]'")
        self._migrate_add_column("matches", "result_json", "TEXT DEFAULT ''")

        self._migrate_add_column("frames", "anomaly", "INTEGER DEFAULT 0")
        self._migrate_add_column("frames", "consistency_note", "TEXT DEFAULT ''")

        self._migrate_add_column("captures", "capture_mode", "TEXT DEFAULT 'full_match'")
        self._migrate_add_column("captures", "task_id", "TEXT DEFAULT 'camera_angle'")

    def _migrate_add_column(self, table: str, column: str, definition: str):
        """Safely add a column if it doesn't exist."""
        try:
            self.conn.execute(f"SELECT {column} FROM {table} LIMIT 1")
        except Exception:
            try:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                self.conn.commit()
            except Exception:
                pass  # Column might already exist

    # ── Match CRUD ──

    def get_all_matches(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM matches ORDER BY match_day ASC, date ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_match(self, match_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        return dict(row) if row else None

    def add_match(self, **kwargs) -> int:
        """Insert a match. Returns the new match id."""
        kwargs["updated_at"] = datetime.now().isoformat()
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        cur = self.conn.execute(
            f"INSERT INTO matches ({cols}) VALUES ({placeholders})",
            list(kwargs.values())
        )
        self.conn.commit()
        return cur.lastrowid

    def update_match(self, match_id: int, **kwargs):
        kwargs["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        self.conn.execute(
            f"UPDATE matches SET {sets} WHERE id = ?",
            list(kwargs.values()) + [match_id]
        )
        self.conn.commit()

    def delete_match(self, match_id: int):
        self.conn.execute("DELETE FROM matches WHERE id = ?", (match_id,))
        self.conn.commit()

    # ── Import from Excel ──

    def import_from_excel(self, excel_path: str, team_name: str, season: str, competition: str):
        """
        Import matches from an Excel file (same format as the existing xlsx).
        Skips duplicates based on (match_day, opponent, date).
        """
        from backend.excel_manager import ExcelManager
        em = ExcelManager(excel_path)
        matches = em.get_all_matches()
        imported = 0
        for m in matches:
            existing = self.conn.execute(
                "SELECT id FROM matches WHERE match_day = ? AND opponent = ? AND date = ?",
                (m.get("md", 0), m.get("opponent", ""), m.get("date", ""))
            ).fetchone()
            if existing:
                continue
            self.add_match(
                match_day=m.get("md", 0),
                date=m.get("date", ""),
                home_away=m.get("home_away", ""),
                opponent=m.get("opponent", ""),
                score=m.get("score", ""),
                result=m.get("result", ""),
                competition=competition,
                season=season,
                team_name=team_name,
                footballia_url=m.get("footballia_url", ""),
                starting_xi=m.get("starting_xi", ""),
                substitutes=m.get("substitutes", ""),
                goal_scorers=m.get("goal_scorers", ""),
                referee=m.get("referee", ""),
            )
            imported += 1
        return imported

    # ── Capture tracking ──

    def create_capture(self, match_id: int, provider: str, source_type: str, config: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO captures (match_id, status, provider, source_type, config_json) VALUES (?, 'in_progress', ?, ?, ?)",
            (match_id, provider, source_type, json.dumps(config))
        )
        self.conn.commit()
        return cur.lastrowid

    def record_frame(self, capture_id: int, filename: str, filepath: str,
                     video_time: float, video_part: int, classification: dict):
        """Called immediately after each frame is saved. Crash-safe."""
        self.conn.execute(
            """INSERT INTO frames
               (capture_id, filename, filepath, video_time, video_part,
                camera_type, confidence, players_visible, pitch_visible_pct,
                is_replay, raw_response)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (capture_id, filename, filepath, video_time, video_part,
             classification.get("camera_type", ""),
             classification.get("confidence", 0.0),
             classification.get("players_visible", 0),
             classification.get("pitch_visible_pct", 0),
             int(classification.get("is_replay", False)),
             json.dumps(classification))
        )
        self.conn.commit()

    def complete_capture(self, capture_id: int, total_captured: int,
                         total_classified: int, api_cost: float,
                         output_dir: str, duration: float):
        self.conn.execute(
            """UPDATE captures SET status = 'completed', total_captured = ?,
               total_classified = ?, api_cost = ?, output_dir = ?,
               duration_seconds = ?, completed_at = datetime('now')
               WHERE id = ?""",
            (total_captured, total_classified, api_cost, output_dir, duration, capture_id)
        )
        self.conn.commit()

    def fail_capture(self, capture_id: int, error_msg: str = ""):
        self.conn.execute(
            "UPDATE captures SET status = 'failed', completed_at = datetime('now') WHERE id = ?",
            (capture_id,)
        )
        self.conn.commit()

    def get_capture_frames(self, capture_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM frames WHERE capture_id = ? ORDER BY video_time", (capture_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Scraped data storage ──

    def update_match_scraped_data(self, match_id: int, scraped: dict):
        """Store scraped Footballia data on a match record."""
        updates = {}
        if scraped.get("home_lineup"):
            updates["home_lineup_json"] = json.dumps(scraped["home_lineup"], ensure_ascii=False)
        if scraped.get("away_lineup"):
            updates["away_lineup_json"] = json.dumps(scraped["away_lineup"], ensure_ascii=False)
        if scraped.get("home_coach"):
            updates["home_coach_json"] = json.dumps(scraped["home_coach"], ensure_ascii=False)
        if scraped.get("away_coach"):
            updates["away_coach_json"] = json.dumps(scraped["away_coach"], ensure_ascii=False)
        if scraped.get("goals"):
            updates["goals_json"] = json.dumps(scraped["goals"], ensure_ascii=False)
        if scraped.get("result"):
            updates["result_json"] = json.dumps(scraped["result"], ensure_ascii=False)
        if scraped.get("venue"):
            updates["venue"] = scraped["venue"]
        if scraped.get("stage"):
            updates["stage"] = scraped["stage"]
        if scraped.get("date") and not self.get_match(match_id).get("date"):
            updates["date"] = scraped["date"]
        if scraped.get("competition") and not self.get_match(match_id).get("competition"):
            updates["competition"] = scraped["competition"]

        if updates:
            self.update_match(match_id, **updates)

    # ── Frame review ──

    def get_frames_for_review(self, capture_id: int,
                              max_confidence: float = 1.0,
                              only_anomalies: bool = False,
                              only_unreviewed: bool = False) -> list[dict]:
        """Get frames that need review, with optional filters."""
        conditions = ["capture_id = ?"]
        params: list = [capture_id]

        if max_confidence < 1.0:
            conditions.append("confidence < ?")
            params.append(max_confidence)

        if only_anomalies:
            conditions.append("anomaly = 1")

        if only_unreviewed:
            conditions.append("is_reviewed = 0")

        where = " AND ".join(conditions)
        rows = self.conn.execute(
            f"SELECT * FROM frames WHERE {where} ORDER BY video_time",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_frames(self, capture_id: int) -> list[dict]:
        """Get all frames classified as PENDING (Manual mode)."""
        rows = self.conn.execute(
            "SELECT * FROM frames WHERE capture_id = ? AND camera_type = 'PENDING' ORDER BY video_time",
            (capture_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def review_frame(self, frame_id: int, new_type: str):
        """Mark a frame as reviewed with a (possibly corrected) classification."""
        self.conn.execute(
            "UPDATE frames SET is_reviewed = 1, reviewed_type = ?, camera_type = ? WHERE id = ?",
            (new_type, new_type, frame_id),
        )
        self.conn.commit()

    def batch_accept_frames(self, capture_id: int, min_confidence: float):
        """Accept all frames above a confidence threshold as reviewed."""
        self.conn.execute(
            """UPDATE frames SET is_reviewed = 1, reviewed_type = camera_type
               WHERE capture_id = ? AND confidence >= ? AND is_reviewed = 0""",
            (capture_id, min_confidence),
        )
        self.conn.commit()
        return self.conn.total_changes

    def get_review_stats(self, capture_id: int) -> dict:
        """Get review progress statistics for a capture."""
        row = self.conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN is_reviewed = 1 THEN 1 ELSE 0 END) as reviewed,
                SUM(CASE WHEN is_reviewed = 0 THEN 1 ELSE 0 END) as unreviewed,
                SUM(CASE WHEN anomaly = 1 THEN 1 ELSE 0 END) as anomalies,
                SUM(CASE WHEN camera_type = 'PENDING' THEN 1 ELSE 0 END) as pending,
                AVG(confidence) as avg_confidence
            FROM frames WHERE capture_id = ?
        """, (capture_id,)).fetchone()
        return dict(row) if row else {}

    def close(self):
        self.conn.close()
