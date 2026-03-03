"""Tests for the annotation bridge output generator."""


class TestAnnotationBridge:

    def test_generates_metadata_json(self, tmp_dir):
        from backend.annotation_bridge import AnnotationBridge

        frames = [
            {"filename": "f1.jpg", "filepath": str(tmp_dir / "WIDE_CENTER" / "f1.jpg"),
             "video_time": 90.0, "video_part": 1, "camera_type": "WIDE_CENTER",
             "confidence": 0.95, "is_reviewed": 0, "reviewed_type": "",
             "is_replay": False, "raw_response": "{}"},
        ]
        # Create the actual file so bridge can symlink it
        (tmp_dir / "WIDE_CENTER").mkdir()
        (tmp_dir / "WIDE_CENTER" / "f1.jpg").write_bytes(b"\xff\xd8test")

        bridge = AnnotationBridge(
            output_dir=str(tmp_dir),
            match_data={"opponent": "Test FC", "date": "2024-01-01"},
            capture_data={"provider": "openai", "task_id": "camera_angle"},
        )
        bridge.generate(frames)

        meta_path = tmp_dir / "annotation_ready" / "screenshotter_metadata.json"
        assert meta_path.exists()
        import json
        meta = json.loads(meta_path.read_text())
        assert meta["source_tool"] == "footballia-screenshotter"
        assert "f1.jpg" in meta["frames"]

    def test_generates_bridge_json(self, tmp_dir):
        from backend.annotation_bridge import AnnotationBridge
        bridge = AnnotationBridge(str(tmp_dir), {}, {})
        bridge.generate([])
        bridge_path = tmp_dir / "annotation_ready" / "annotation_bridge.json"
        assert bridge_path.exists()

    def test_generates_roster_csv(self, tmp_dir):
        from backend.annotation_bridge import AnnotationBridge
        bridge = AnnotationBridge(str(tmp_dir), {}, {})
        scraped = {
            "home_team": "Juventus FC",
            "away_team": "Celtic FC",
            "season": "1981-1982",
            "home_lineup": [
                {"number": 1, "name": "Zoff", "age": 39},
                {"number": 2, "name": "Gentile", "age": 28},
            ],
            "away_lineup": [
                {"number": 1, "name": "Bonner", "age": 30},
            ],
            "home_coach": {"name": "Trapattoni", "age": 42},
        }
        bridge.generate([], scraped)
        rosters_dir = tmp_dir / "annotation_ready" / "rosters"
        assert rosters_dir.exists()
        csvs = list(rosters_dir.glob("*.csv"))
        assert len(csvs) == 2  # home + away
        # Check home roster content
        home_csv = next(c for c in csvs if "home" in c.name)
        content = home_csv.read_text()
        assert "Zoff" in content
        assert "Trapattoni" in content
        assert "coach" in content

    def test_default_bridge_mapping(self):
        from backend.annotation_bridge import DEFAULT_BRIDGE_MAPPING
        assert DEFAULT_BRIDGE_MAPPING["WIDE_CENTER"]["shot_type"] == "wide"
        assert DEFAULT_BRIDGE_MAPPING["OTHER"]["auto_skip"] is True
