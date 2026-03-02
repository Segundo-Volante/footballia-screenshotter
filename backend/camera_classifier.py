import asyncio
import base64
import json

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from backend.utils import logger, get_openai_key, CAMERA_TYPES

CLASSIFICATION_PROMPT = """Classify this football broadcast screenshot into ONE camera angle.

Categories:
- WIDE_CENTER: Main camera, central elevated, 8+ players, full pitch width visible
- WIDE_LEFT: Broadcast camera panned left, large pitch area visible
- WIDE_RIGHT: Broadcast camera panned right, large pitch area visible
- MEDIUM: Tighter shot, 3-7 players, focused on ball area
- CLOSEUP: Tight on 1-2 people, face/celebration/injury, minimal pitch
- BEHIND_GOAL: Camera behind goal line looking down pitch, goal posts visible
- AERIAL: Top-down/steep overhead, spider cam, bird's eye formations
- OTHER: Crowd, scoreboard, graphics, tunnel, replay transition, studio

JSON only: {"camera_type":"WIDE_CENTER","confidence":0.95,"players_visible":18,"pitch_visible_pct":80,"is_replay":false}"""


class CameraClassifier:
    def __init__(self, config: dict, targets: dict):
        self.client = AsyncOpenAI(api_key=get_openai_key())
        self.model = config.get("model", "gpt-4o-mini")
        self.detail = config.get("detail", "low")
        self.cost_per_frame = config.get("cost_per_frame", 0.00007)
        self.max_concurrent = config.get("max_concurrent", 3)
        self._semaphore = asyncio.Semaphore(self.max_concurrent)

        self.targets = dict(targets)
        self.counts = {t: 0 for t in CAMERA_TYPES}
        self.total_tokens = 0
        self.total_classified = 0

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((Exception,)),
        reraise=True,
    )
    async def _call_api(self, b64_image: str) -> dict:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": CLASSIFICATION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_image}",
                                "detail": self.detail,
                            },
                        },
                    ],
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=100,
        )

        if response.usage:
            self.total_tokens += response.usage.total_tokens

        content = response.choices[0].message.content
        return json.loads(content)

    async def classify_frame(self, jpeg_bytes: bytes) -> dict:
        async with self._semaphore:
            b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
            try:
                result = await self._call_api(b64)
            except Exception as e:
                logger.error(f"Classification failed after retries: {e}")
                return {
                    "camera_type": "OTHER",
                    "confidence": 0.0,
                    "players_visible": 0,
                    "pitch_visible_pct": 0,
                    "is_replay": False,
                    "error": str(e),
                }

            camera_type = result.get("camera_type", "OTHER").upper()
            if camera_type not in CAMERA_TYPES:
                camera_type = "OTHER"

            self.total_classified += 1
            self.counts[camera_type] = self.counts.get(camera_type, 0) + 1

            return {
                "camera_type": camera_type,
                "confidence": float(result.get("confidence", 0.0)),
                "players_visible": int(result.get("players_visible", 0)),
                "pitch_visible_pct": int(result.get("pitch_visible_pct", 0)),
                "is_replay": bool(result.get("is_replay", False)),
            }

    def should_save(self, camera_type: str) -> bool:
        target = self.targets.get(camera_type, 0)
        if target <= 0:
            return False
        saved_count = self.counts.get(camera_type, 0)
        # counts was already incremented in classify_frame, so compare with target
        # We need to track saved separately from classified
        return True  # Decision is made in pipeline with separate saved counts

    def all_targets_met(self, saved_counts: dict) -> bool:
        for cam_type, target in self.targets.items():
            if target > 0 and saved_counts.get(cam_type, 0) < target:
                return False
        return True

    def get_cost(self) -> float:
        return self.total_classified * self.cost_per_frame

    def get_progress(self, saved_counts: dict) -> dict:
        return {
            "counts": {
                cam: {"target": self.targets.get(cam, 0), "captured": saved_counts.get(cam, 0)}
                for cam in CAMERA_TYPES
            },
            "total_classified": self.total_classified,
            "api_cost": self.get_cost(),
        }
