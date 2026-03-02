"""
OpenAI GPT-4o-mini classifier.
Refactored from the original camera_classifier.py to work with any task template.
"""
import base64
import json
import logging
from openai import AsyncOpenAI
from .base import BaseClassifier

logger = logging.getLogger(__name__)

# Cost per frame: input (image ~800 tokens + prompt ~400 tokens) + output (~80 tokens)
# GPT-4o-mini: $0.15/M input, $0.60/M output
# ~$0.00018 input + $0.000048 output ≈ $0.00007 per frame (conservative)
COST_PER_FRAME = 0.00007


class OpenAIClassifier(BaseClassifier):

    def __init__(self, task: dict, config: dict):
        super().__init__(task, config)
        api_key = config.get("api_key", "")
        if not api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY in your .env file.")
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = config.get("model", "gpt-4o-mini")
        self._max_concurrent = config.get("max_concurrent", 3)

    async def classify_frame(self, jpeg_bytes: bytes) -> dict:
        b64 = base64.b64encode(jpeg_bytes).decode("utf-8")

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "low",
                            },
                        },
                    ],
                }],
                max_tokens=200,
                temperature=0.1,
            )

            self._call_count += 1
            self._total_cost += COST_PER_FRAME

            text = response.choices[0].message.content.strip()
            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            raw = json.loads(text)
            return self._standardize_result(raw)

        except json.JSONDecodeError as e:
            logger.warning(f"OpenAI returned non-JSON: {text[:200]}... Error: {e}")
            return {
                "classified_as": "OTHER" if "OTHER" in self.categories else self.categories[-1],
                "confidence": 0.0,
                "raw_response": {"raw_text": text[:500]},
                "reasoning": f"JSON parse error: {e}",
                "parse_error": True,
            }
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return {
                "classified_as": "OTHER" if "OTHER" in self.categories else self.categories[-1],
                "confidence": 0.0,
                "raw_response": {"error": str(e)},
                "reasoning": f"API error: {e}",
                "api_error": True,
            }

    def get_provider_name(self) -> str:
        return "openai"
