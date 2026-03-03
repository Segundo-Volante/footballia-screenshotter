"""Tests for the task template manager."""
import json


class TestTaskManager:

    def test_loads_default_tasks(self):
        from backend.task_manager import TaskManager
        tm = TaskManager()
        tasks = tm.get_all_tasks()
        assert len(tasks) >= 4  # camera_angle, formation, event, scene

    def test_get_task_by_id(self):
        from backend.task_manager import TaskManager
        tm = TaskManager()
        task = tm.get_task("camera_angle")
        assert task is not None
        assert "WIDE_CENTER" in task["categories"]
        assert len(task["prompt"]) > 50

    def test_get_preset_targets(self):
        from backend.task_manager import TaskManager
        tm = TaskManager()
        targets = tm.get_preset_targets("camera_angle", "training_data")
        assert targets is not None
        assert "WIDE_CENTER" in targets

    def test_validate_valid_task(self):
        from backend.task_manager import TaskManager
        tm = TaskManager()
        task = {
            "id": "test_task",
            "name": "Test",
            "classification_field": "test_field",
            "categories": ["A", "B"],
            "prompt": "Classify this image and respond in JSON format",
        }
        errors = tm.validate_task(task)
        assert len(errors) == 0

    def test_validate_invalid_task(self):
        from backend.task_manager import TaskManager
        tm = TaskManager()
        errors = tm.validate_task({"id": "x"})
        assert len(errors) > 0

    def test_save_custom_task(self, tmp_dir):
        from backend.task_manager import TaskManager
        tm = TaskManager(tasks_dir=str(tmp_dir))
        task = {
            "id": "custom_test",
            "name": "Custom Test",
            "classification_field": "test",
            "categories": ["A", "B"],
            "prompt": "Test",
        }
        path = tm.save_custom_task(task)
        assert (tmp_dir / "custom_test.json").exists()
        loaded = json.loads((tmp_dir / "custom_test.json").read_text())
        assert loaded["name"] == "Custom Test"
