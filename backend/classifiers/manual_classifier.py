"""
Manual classifier — no API calls.
All frames that pass the pre-filter are saved with classified_as="PENDING".
User classifies them manually in the Gallery/Review UI (Part 3).

This enables the tool to work with zero API keys and zero cost.
"""
import logging
from .base import BaseClassifier

logger = logging.getLogger(__name__)


class ManualClassifier(BaseClassifier):

    def __init__(self, task: dict, config: dict):
        super().__init__(task, config)

    async def classify_frame(self, jpeg_bytes: bytes) -> dict:
        """
        Does not classify. Returns PENDING status.
        The frame will be saved by pipeline.py into the PENDING folder.
        """
        self._call_count += 1
        return {
            "classified_as": "PENDING",
            "confidence": 0.0,
            "raw_response": {},
            "reasoning": "Manual mode — awaiting human classification",
            "is_pending": True,
        }

    def get_provider_name(self) -> str:
        return "manual"
