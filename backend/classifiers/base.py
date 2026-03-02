"""
Abstract base class for frame classifiers.

All classifiers (OpenAI, Gemini, Manual) implement this interface.
The base class provides:
- Prompt and category management (loaded from task template)
- Cost tracking
- Result standardization with fuzzy category matching
"""
from abc import ABC, abstractmethod
from difflib import get_close_matches


class BaseClassifier(ABC):

    def __init__(self, task: dict, config: dict):
        """
        Args:
            task: Full task template dict (from TaskManager).
            config: App config dict (from config.yaml).
        """
        self.task = task
        self.config = config
        self.categories: list[str] = [c["value"] for c in task.get("categories", [])]
        self.classification_field: str = task.get("classification_field", "camera_type")
        self.prompt: str = task.get("prompt", "")
        self._call_count = 0
        self._total_cost = 0.0

    @abstractmethod
    async def classify_frame(self, jpeg_bytes: bytes) -> dict:
        """
        Classify a single frame.

        Must return:
        {
            "classified_as": str,       # Category value (e.g. "WIDE_CENTER")
            "confidence": float,        # 0.0 to 1.0
            "raw_response": dict,       # Full API response for debugging
            "reasoning": str,           # Model's reasoning text
            ...extra fields from task (players_visible, etc.)
        }
        """

    @abstractmethod
    def get_provider_name(self) -> str:
        """e.g. 'openai', 'gemini', 'manual'"""

    def get_cost(self) -> float:
        return self._total_cost

    def get_call_count(self) -> int:
        return self._call_count

    def _standardize_result(self, raw: dict) -> dict:
        """
        Normalize a raw API response into the standard format.

        Handles fuzzy matching if the model returns a category name that
        doesn't exactly match (e.g. "wide_center" -> "WIDE_CENTER").
        """
        # Try classification_field first, then common fallbacks
        classified_as = (
            raw.get(self.classification_field)
            or raw.get("classified_as")
            or raw.get("camera_type")
            or raw.get("type")
            or "OTHER"
        )

        # Normalize to uppercase
        classified_as = str(classified_as).upper().strip()

        # Fuzzy match against valid categories
        if classified_as not in self.categories:
            matches = get_close_matches(classified_as, self.categories, n=1, cutoff=0.6)
            if matches:
                classified_as = matches[0]
            elif "OTHER" in self.categories:
                classified_as = "OTHER"
            else:
                classified_as = self.categories[-1] if self.categories else "OTHER"

        confidence = float(raw.get("confidence", 0.0))
        if confidence > 1.0:
            confidence = confidence / 100.0

        result = {
            "classified_as": classified_as,
            "confidence": confidence,
            "raw_response": raw,
            "reasoning": raw.get("reasoning", ""),
        }

        # Carry through extra fields defined in task template
        for field in self.task.get("extra_fields", []):
            if field in raw:
                result[field] = raw[field]

        return result
