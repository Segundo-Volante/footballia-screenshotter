"""Tests for the dataset exporter."""
import json


def _add_test_match(db, **overrides):
    """Helper to add a match with defaults."""
    defaults = dict(
        match_day=1, date="", home_away="H", opponent="Test",
        score="", footballia_url="", team_name="", season="", competition="",
    )
    defaults.update(overrides)
    return db.add_match(**defaults)


class TestExporter:

    def test_csv_export(self, tmp_dir, mock_db):
        from backend.exporter import DatasetExporter
        # Create test data
        mid = _add_test_match(mock_db)
        cid = mock_db.create_capture(mid, "openai", "footballia", {})
        (tmp_dir / "WIDE_CENTER").mkdir()
        (tmp_dir / "WIDE_CENTER" / "f1.jpg").write_bytes(b"\xff\xd8test")
        mock_db.record_frame(cid, "f1.jpg", str(tmp_dir / "WIDE_CENTER" / "f1.jpg"),
                             90.0, 1, {"camera_type": "WIDE_CENTER", "confidence": 0.9})

        exp = DatasetExporter(db=mock_db)
        csv_path = str(tmp_dir / "export.csv")
        exp.export_csv(csv_path)
        assert (tmp_dir / "export.csv").exists()
        content = (tmp_dir / "export.csv").read_text()
        assert "WIDE_CENTER" in content

    def test_coco_export(self, tmp_dir, mock_db):
        from backend.exporter import DatasetExporter
        mid = _add_test_match(mock_db)
        cid = mock_db.create_capture(mid, "openai", "footballia", {})
        (tmp_dir / "WIDE_CENTER").mkdir()
        (tmp_dir / "WIDE_CENTER" / "f1.jpg").write_bytes(b"\xff\xd8test")
        mock_db.record_frame(cid, "f1.jpg", str(tmp_dir / "WIDE_CENTER" / "f1.jpg"),
                             90.0, 1, {"camera_type": "WIDE_CENTER", "confidence": 0.9})

        exp = DatasetExporter(db=mock_db)
        coco_dir = str(tmp_dir / "coco_out")
        exp.export_coco(coco_dir)
        ann_path = tmp_dir / "coco_out" / "annotations.json"
        assert ann_path.exists()
        data = json.loads(ann_path.read_text())
        assert len(data["images"]) == 1
        assert len(data["categories"]) >= 1
