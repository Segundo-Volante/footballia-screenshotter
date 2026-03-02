"""
Abstract base class for frame classifiers.

Part 1 creates the interface.
Part 2 implements OpenAI, Gemini, Manual classifiers.
For now, the existing camera_classifier.py continues to be used directly.
"""
from abc import ABC, abstractmethod


class BaseClassifier(ABC):

    @abstractmethod
    async def classify_frame(self, jpeg_bytes: bytes) -> dict:
        """Classify a frame.
        Must return a dict with at minimum:
        {
            "classified_as": str,     # The category value (e.g. "WIDE_CENTER")
            "confidence": float,      # 0.0 to 1.0
        }
        Additional fields are preserved in raw_response.
        """

    @abstractmethod
    def get_cost(self) -> float:
        """Total API cost so far."""

    @abstractmethod
    def get_provider_name(self) -> str:
        """e.g. 'openai', 'gemini', 'manual', 'local'"""

    @abstractmethod
    def all_targets_met(self, saved_counts: dict) -> bool:
        """True if all category targets have been reached."""
