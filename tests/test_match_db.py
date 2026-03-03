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

    def test_delete_match_cascades(self, mock_db):
        """Deleting a match should cascade-delete captures and frames."""
        mid = _add_test_match(mock_db, opponent="CascadeTest")
        cid = mock_db.create_capture(mid, "openai", "footballia", {})
        mock_db.record_frame(cid, "f1.jpg", "/tmp/f1.jpg", 10.0, 1,
                             {"camera_type": "WIDE", "confidence": 0.9})
        mock_db.record_frame(cid, "f2.jpg", "/tmp/f2.jpg", 20.0, 1,
                             {"camera_type": "MEDIUM", "confidence": 0.8})

        # Verify data exists before delete
        assert len(mock_db.get_capture_frames(cid)) == 2
        assert mock_db.get_match(mid) is not None

        # Delete the match
        mock_db.delete_match(mid)

        # Verify match is gone
        assert mock_db.get_match(mid) is None

        # Verify captures are gone
        cap = mock_db.conn.execute(
            "SELECT * FROM captures WHERE id = ?", (cid,)
        ).fetchone()
        assert cap is None

        # Verify frames are gone
        assert len(mock_db.get_capture_frames(cid)) == 0

    def test_delete_match_cleans_collections(self, mock_db):
        """Deleting a match should remove it from collections."""
        mid = _add_test_match(mock_db, opponent="CollectionTest")

        # Create a collection and add the match
        mock_db.conn.execute(
            "INSERT INTO collections (name, description) VALUES (?, ?)",
            ("Test Collection", "test")
        )
        col_id = mock_db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        mock_db.conn.execute(
            "INSERT INTO collection_matches (collection_id, match_id) VALUES (?, ?)",
            (col_id, mid)
        )
        mock_db.conn.commit()

        # Verify association exists
        assoc = mock_db.conn.execute(
            "SELECT * FROM collection_matches WHERE match_id = ?", (mid,)
        ).fetchone()
        assert assoc is not None

        # Delete the match
        mock_db.delete_match(mid)

        # Verify association is gone
        assoc = mock_db.conn.execute(
            "SELECT * FROM collection_matches WHERE match_id = ?", (mid,)
        ).fetchone()
        assert assoc is None

    def test_delete_match_multiple_captures(self, mock_db):
        """Deleting a match with multiple captures should clean all."""
        mid = _add_test_match(mock_db, opponent="MultiCapture")
        cid1 = mock_db.create_capture(mid, "openai", "footballia", {})
        cid2 = mock_db.create_capture(mid, "gemini", "footballia", {})
        mock_db.record_frame(cid1, "a.jpg", "/tmp/a.jpg", 5.0, 1,
                             {"camera_type": "WIDE", "confidence": 0.9})
        mock_db.record_frame(cid2, "b.jpg", "/tmp/b.jpg", 15.0, 1,
                             {"camera_type": "CLOSE", "confidence": 0.85})

        mock_db.delete_match(mid)

        assert mock_db.get_match(mid) is None
        assert len(mock_db.get_capture_frames(cid1)) == 0
        assert len(mock_db.get_capture_frames(cid2)) == 0

    def test_delete_nonexistent_match(self, mock_db):
        """Deleting a match that doesn't exist should not error."""
        mock_db.delete_match(99999)  # Should not raise

    def test_migration_is_safe(self, mock_db):
        """Calling _create_tables twice should not error."""
        mock_db._create_tables()
        mock_db._create_tables()
