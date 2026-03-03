"""Tests for the batch capture manager."""
import asyncio

class TestBatchManager:

    def test_create_batch(self):
        from backend.batch_manager import BatchManager
        bm = BatchManager()
        matches = [
            {"id": 1, "opponent": "Team A", "match_day": 1, "home_away": "H"},
            {"id": 2, "opponent": "Team B", "match_day": 2, "home_away": "A"},
        ]
        batch_id = bm.create_batch(matches, {"WIDE_CENTER": 10}, "openai", "camera_angle", "full_match")
        assert batch_id.startswith("batch_")

    def test_get_state(self):
        from backend.batch_manager import BatchManager
        bm = BatchManager()
        bm.create_batch(
            [{"id": 1, "opponent": "X", "match_day": 1, "home_away": "H"}],
            {}, "openai", "camera_angle", "full_match",
        )
        state = bm.get_state()
        assert state["total"] == 1
        assert state["status"] == "pending"

    def test_cancel(self):
        from backend.batch_manager import BatchManager
        bm = BatchManager()
        bm.create_batch([], {}, "manual", "camera_angle", "full_match")
        bm.cancel()
        assert bm._cancelled

    def test_match_label_generation(self):
        from backend.batch_manager import BatchManager
        label = BatchManager._match_label({
            "match_day": 5, "opponent": "Valencia", "home_away": "H"
        })
        assert "MD5" in label
        assert "Valencia" in label
        assert "(H)" in label

    def test_match_label_fallback(self):
        from backend.batch_manager import BatchManager
        label = BatchManager._match_label({"away_team": "Celtic"})
        assert "Celtic" in label

    def test_save_and_load_state(self, tmp_dir):
        from backend.batch_manager import BatchManager
        import json

        bm = BatchManager()
        bm.STATE_FILE = tmp_dir / "batch_state.json"
        bm.create_batch(
            [{"id": 1, "opponent": "Test", "match_day": 1, "home_away": "H", "footballia_url": "http://test"}],
            {"WIDE_CENTER": 10}, "openai", "camera_angle", "full_match",
        )
        bm._save_state()
        assert bm.STATE_FILE.exists()

        # Load in a new instance
        bm2 = BatchManager()
        bm2.STATE_FILE = tmp_dir / "batch_state.json"
        loaded = bm2.load_state()
        state = bm2.get_state()
        assert state["total"] == 1
