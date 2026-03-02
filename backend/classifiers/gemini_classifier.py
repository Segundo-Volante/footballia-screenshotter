"""
Google Gemini Flash classifier.
Free tier: 15 RPM, 1,500 requests/day, 1M tokens/day.
Paid: $0.075/M input, $0.30/M output (roughly half the cost of GPT-4o-mini).

Requires: pip install google-generativeai
"""
import json
import logging
from .base import BaseClassifier

logger = logging.getLogger(__name__)

COST_PER_FRAME_FREE = 0.0  # Within free tier
COST_PER_FRAME_PAID = 0.00004  # Roughly half of GPT-4o-mini


class GeminiClassifier(BaseClassifier):

    def __init__(self, task: dict, config: dict):
        super().__init__(task, config)
        api_key = config.get("gemini_api_key", "")
        if not api_key:
            raise ValueError(
                "Google Gemini API key is required. "
                "Get a free key at https://aistudio.google.com/apikey and set GEMINI_API_KEY in .env"
            )
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError(
                "google-generativeai package not installed. "
                "Run: pip install google-generativeai"
            )

        genai.configure(api_key=api_key)
        self._model_name = config.get("gemini_model", "gemini-2.0-flash")
        self._model = genai.GenerativeModel(
            self._model_name,
            generation_config=genai.GenerationConfig(
                temperature=0.1,
                max_output_tokens=200,
                response_mime_type="application/json",
            ),
        )
        self._is_free_tier = config.get("gemini_free_tier", True)

    async def classify_frame(self, jpeg_bytes: bytes) -> dict:
        try:
            # Gemini accepts raw bytes with mime type
            image_part = {
                "mime_type": "image/jpeg",
                "data": jpeg_bytes,
            }

            # Gemini's generate_content is sync; run in thread for async
            import asyncio
            response = await asyncio.to_thread(
                self._model.generate_content,
                [self.prompt, image_part],
            )

            self._call_count += 1
            if not self._is_free_tier:
                self._total_cost += COST_PER_FRAME_PAID

            text = response.text.strip()
            # Strip markdown fences
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            raw = json.loads(text)
            return self._standardize_result(raw)

        except json.JSONDecodeError as e:
            logger.warning(f"Gemini returned non-JSON: {text[:200]}... Error: {e}")
            return {
                "classified_as": "OTHER" if "OTHER" in self.categories else self.categories[-1],
                "confidence": 0.0,
                "raw_response": {"raw_text": text[:500]},
                "reasoning": f"JSON parse error: {e}",
                "parse_error": True,
            }
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            return {
                "classified_as": "OTHER" if "OTHER" in self.categories else self.categories[-1],
                "confidence": 0.0,
                "raw_response": {"error": str(e)},
                "reasoning": f"API error: {e}",
                "api_error": True,
            }

    def get_provider_name(self) -> str:
        return "gemini"
