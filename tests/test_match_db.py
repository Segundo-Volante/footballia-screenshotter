"""Tests for the SQLite match database."""


def _add_test_match(db, **overrides):
    """Helper to add a match with defaults."""
    defaults = dict(
        match_day=1, date="", home_away="H", opponent="Test",
        score="", footballia_url="", team_name="", season="", competition="",
    )
    defaults.update(overrides)
    return db.add_match(**defaults)


class TestMatchDB:

    def test_create_match(self, mock_db):
        mid = mock_db.add_match(
            match_day=1, date="2024-08-19", home_away="A",
            opponent="Villarreal", score="2-2", footballia_url="https://footballia.eu/test",
            team_name="Atletico", season="2024-2025", competition="La Liga",
        )
        assert mid > 0

    def test_get_match(self, mock_db):
        mid = mock_db.add_match(match_day=1, date="", home_away="H",
                                opponent="Test", score="", footballia_url="",
                                team_name="", season="", competition="")
        match = mock_db.get_match(mid)
        assert match["opponent"] == "Test"

    def test_get_all_matches(self, mock_db):
        _add_test_match(mock_db, match_day=1, opponent="TeamA")
        _add_test_match(mock_db, match_day=2, opponent="TeamB", home_away="A")
        matches = mock_db.get_all_matches()
        assert len(matches) == 2

    def test_create_capture(self, mock_db):
        mid = _add_test_match(mock_db)
        cid = mock_db.create_capture(match_id=mid, provider="openai",
                                      source_type="footballia", config={})
        assert cid > 0

    def test_record_frame(self, mock_db):
        mid = _add_test_match(mock_db)
        cid = mock_db.create_capture(mid, "openai", "footballia", {})
        mock_db.record_frame(cid, "frame_001.jpg", "/tmp/frame_001.jpg", 90.0, 1,
                             {"camera_type": "WIDE_CENTER", "confidence": 0.95})
        frames = mock_db.get_capture_frames(cid)
        assert len(frames) == 1
        assert frames[0]["camera_type"] == "WIDE_CENTER"

    def test_review_frame(self, mock_db):
        mid = _add_test_match(mock_db)
        cid = mock_db.create_capture(mid, "manual", "footballia", {})
        mock_db.record_frame(cid, "frame_001.jpg", "/tmp/f.jpg", 0.0, 1,
                             {"camera_type": "PENDING", "confidence": 0.0})
        frames = mock_db.get_capture_frames(cid)
        fid = frames[0]["id"]
        mock_db.review_frame(fid, "MEDIUM")
        updated = mock_db.get_capture_frames(cid)
        assert updated[0]["camera_type"] == "MEDIUM"
        assert updated[0]["is_reviewed"] == 1

    def test_batch_accept(self, mock_db):
        mid = _add_test_match(mock_db)
        cid = mock_db.create_capture(mid, "openai", "footballia", {})
        for i in range(5):
            conf = 0.5 + i * 0.1  # 0.5, 0.6, 0.7, 0.8, 0.9
            mock_db.record_frame(cid, f"f_{i}.jpg", f"/tmp/f_{i}.jpg", i * 10.0, 1,
                                 {"camera_type": "WIDE_CENTER", "confidence": conf})
        accepted = mock_db.batch_accept_frames(cid, 0.75)
        assert accepted == 2  # frames with conf 0.8 and 0.9

    def test_migration_is_safe(self, mock_db):
        """Calling _create_tables twice should not error."""
        mock_db._create_tables()
        mock_db._create_tables()
