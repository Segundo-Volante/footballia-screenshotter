"""
Task Manager — loads analysis task templates from config/tasks/.
Each task defines what the AI should classify, its categories, prompt, and presets.
Also supports user-created custom tasks.
"""
import json
from pathlib import Path
from typing import Optional

TASKS_DIR = Path(__file__).parent.parent / "config" / "tasks"


class TaskManager:
    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._load_tasks()

    def _load_tasks(self):
        """Load all .json files from config/tasks/"""
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        for f in sorted(TASKS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                task_id = data.get("id", f.stem)
                data["id"] = task_id
                data["_source"] = str(f)
                self._tasks[task_id] = data
            except Exception as e:
                print(f"Warning: failed to load task {f.name}: {e}")

    def get_task(self, task_id: str) -> Optional[dict]:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[dict]:
        """Return summary of all tasks (without full prompt text) for UI listing."""
        result = []
        for t in self._tasks.values():
            result.append({
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "categories": t["categories"],
                "category_descriptions": t.get("category_descriptions", {}),
                "suggested_targets": t.get("suggested_targets", {}),
                "presets": {
                    k: {"name": v["name"], "description": v["description"]}
                    for k, v in t.get("presets", {}).items()
                },
            })
        return result

    def get_task_prompt(self, task_id: str) -> str:
        task = self._tasks.get(task_id)
        if not task:
            return ""
        return task.get("prompt", "")

    def get_task_categories(self, task_id: str) -> list[str]:
        task = self._tasks.get(task_id)
        if not task:
            return []
        return task.get("categories", [])

    def get_classification_field(self, task_id: str) -> str:
        task = self._tasks.get(task_id)
        if not task:
            return "camera_type"
        return task.get("classification_field", "camera_type")

    def get_preset_targets(self, task_id: str, preset_id: str) -> Optional[dict]:
        task = self._tasks.get(task_id)
        if not task:
            return None
        preset = task.get("presets", {}).get(preset_id)
        if not preset:
            return None
        return preset["targets"]

    def save_custom_task(self, task_data: dict) -> str:
        """Save a user-defined custom task. Returns the task id."""
        task_id = task_data.get("id", "custom_" + str(len(self._tasks)))
        task_data["id"] = task_id
        filepath = TASKS_DIR / f"{task_id}.json"
        filepath.write_text(
            json.dumps(task_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._tasks[task_id] = task_data
        return task_id

    def validate_task(self, task_data: dict) -> list[str]:
        """Validate a task template. Returns list of error messages (empty = valid)."""
        errors = []
        if not task_data.get("id"):
            errors.append("Missing 'id'")
        if not task_data.get("name"):
            errors.append("Missing 'name'")
        if not task_data.get("classification_field"):
            errors.append("Missing 'classification_field'")
        cats = task_data.get("categories", [])
        if len(cats) < 2:
            errors.append("Need at least 2 categories")
        if not task_data.get("prompt"):
            errors.append("Missing 'prompt'")
        prompt = task_data.get("prompt", "")
        if "JSON" not in prompt and "json" not in prompt:
            errors.append("Prompt should instruct the model to respond in JSON")
        return errors
