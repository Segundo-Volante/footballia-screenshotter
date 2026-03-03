"""Tests for the output manager module."""
import asyncio
import os
from pathlib import Path


class TestOutputManager:

    def _make_om(self, tmp_dir, categories):
        from backend.output_manager import OutputManager
        match = {"md": 1, "opponent": "TestTeam", "date": "2024-01-15"}
        return OutputManager(match, str(tmp_dir), categories)

    def test_creates_category_folders(self, tmp_dir):
        categories = ["WIDE_CENTER", "MEDIUM", "CLOSEUP", "OTHER"]
        om = self._make_om(tmp_dir, categories)

        for cat in categories:
            assert (Path(om.get_output_dir()) / cat).is_dir()

    def test_save_frame(self, tmp_dir, sample_jpeg):
        om = self._make_om(tmp_dir, ["WIDE_CENTER"])
        classification = {"classified_as": "WIDE_CENTER", "confidence": 0.95}
        filepath = asyncio.get_event_loop().run_until_complete(
            om.save_frame(sample_jpeg, 90.0, classification, 1)
        )

        assert str(filepath).endswith(".jpg")
        assert os.path.exists(filepath)
        assert "WIDE_CENTER" in str(filepath)

    def test_save_to_pending(self, tmp_dir, sample_jpeg):
        om = self._make_om(tmp_dir, ["PENDING"])
        filepath = om.save_frame_to_pending(sample_jpeg, 45.5)

        assert (Path(om.get_output_dir()) / "PENDING").exists()
        assert os.path.exists(filepath)

    def test_move_frame(self, tmp_dir, sample_jpeg):
        om = self._make_om(tmp_dir, ["WIDE_CENTER", "MEDIUM"])

        classification = {"classified_as": "WIDE_CENTER", "confidence": 0.9}
        filepath = asyncio.get_event_loop().run_until_complete(
            om.save_frame(sample_jpeg, 10.0, classification, 1)
        )
        assert os.path.exists(filepath)

        new_path = om.move_frame(str(filepath), "MEDIUM")
        assert os.path.exists(new_path)
        assert not os.path.exists(str(filepath))
        assert "MEDIUM" in new_path

    def test_metadata_csv(self, tmp_dir, sample_jpeg):
        om = self._make_om(tmp_dir, ["WIDE_CENTER"])
        classification = {"classified_as": "WIDE_CENTER", "confidence": 0.9}
        asyncio.get_event_loop().run_until_complete(
            om.save_frame(sample_jpeg, 90.0, classification, 1)
        )
        asyncio.get_event_loop().run_until_complete(
            om.save_frame(sample_jpeg, 92.0, classification, 1)
        )

        om.generate_metadata_csv()
        csv_path = Path(om.get_output_dir()) / "metadata.csv"
        assert csv_path.exists()
        content = csv_path.read_text()
        assert "WIDE_CENTER" in content

    def test_get_output_dir(self, tmp_dir):
        om = self._make_om(tmp_dir, ["A"])
        output_dir = om.get_output_dir()
        assert os.path.isdir(output_dir)
        assert str(tmp_dir) in output_dir
