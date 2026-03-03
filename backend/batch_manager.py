"""
Batch Capture Manager — queues and executes multiple captures sequentially.

When a user selects multiple matches and clicks "Batch Capture":
1. All matches are added to a queue
2. Each match is captured one at a time (sequential, not parallel)
3. Between matches, a configurable delay is applied (default 30s for Footballia rate limiting)
4. If one match fails, the queue continues to the next
5. Real-time progress is broadcast via WebSocket

The batch state is persisted to disk so it survives server restarts.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class BatchItemStatus(str, Enum):
    PENDING = "pending"
    CAPTURING = "capturing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class BatchItem:
    match_id: int
    match_label: str  # e.g. "MD05 Valencia (H)"
    footballia_url: str
    status: str = BatchItemStatus.PENDING
    frames_captured: int = 0
    api_cost: float = 0.0
    error_message: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0


@dataclass
class BatchState:
    batch_id: str
    items: list[BatchItem] = field(default_factory=list)
    current_index: int = 0
    status: str = "pending"  # pending, running, paused, completed, cancelled
    created_at: float = field(default_factory=time.time)
    targets: dict = field(default_factory=dict)
    provider: str = "openai"
    task_id: str = "camera_angle"
    capture_mode: str = "full_match"
    delay_between: int = 30  # Seconds between captures (rate limiting)


class BatchManager:
    """
    Manages a batch capture queue.
    Only one batch can run at a time (the Pipeline is single-threaded).
    """

    STATE_FILE = Path("data/batch_state.json")

    def __init__(self, broadcast_fn: Optional[Callable] = None):
        self._broadcast_fn = broadcast_fn
        self._state: Optional[BatchState] = None
        self._cancelled = False
        self._paused = False

    def create_batch(self, matches: list[dict], targets: dict,
                     provider: str, task_id: str, capture_mode: str,
                     delay_between: int = 30) -> str:
        """
        Create a new batch from a list of matches.
        Returns batch_id.
        """
        import uuid
        batch_id = f"batch_{uuid.uuid4().hex[:8]}"

        items = []
        for m in matches:
            items.append(BatchItem(
                match_id=m.get("id", 0),
                match_label=self._match_label(m),
                footballia_url=m.get("footballia_url", m.get("full_url", "")),
            ))

        self._state = BatchState(
            batch_id=batch_id,
            items=items,
            targets=targets,
            provider=provider,
            task_id=task_id,
            capture_mode=capture_mode,
            delay_between=delay_between,
        )
        self._save_state()
        return batch_id

    async def run(self, pipeline_factory: Callable):
        """
        Execute the batch queue.

        Args:
            pipeline_factory: async function(match_data, targets, provider, ...) -> Pipeline
                Called for each match to create and run a Pipeline instance.
        """
        if not self._state:
            return

        self._state.status = "running"
        self._cancelled = False
        self._paused = False

        await self._broadcast({
            "type": "batch_started",
            "batch_id": self._state.batch_id,
            "total": len(self._state.items),
        })

        for i, item in enumerate(self._state.items):
            if self._cancelled:
                item.status = BatchItemStatus.SKIPPED
                continue

            while self._paused:
                await asyncio.sleep(1)
                if self._cancelled:
                    break

            if self._cancelled:
                item.status = BatchItemStatus.SKIPPED
                continue

            self._state.current_index = i
            item.status = BatchItemStatus.CAPTURING
            item.started_at = time.time()

            await self._broadcast({
                "type": "batch_item_started",
                "index": i,
                "total": len(self._state.items),
                "match_label": item.match_label,
            })

            try:
                # Create and run pipeline for this match
                result = await pipeline_factory(
                    match_url=item.footballia_url,
                    match_id=item.match_id,
                    match_label=item.match_label,
                    targets=self._state.targets,
                    provider=self._state.provider,
                    task_id=self._state.task_id,
                    capture_mode=self._state.capture_mode,
                )

                item.status = BatchItemStatus.COMPLETED
                item.frames_captured = result.get("frames_captured", 0)
                item.api_cost = result.get("api_cost", 0.0)

            except Exception as e:
                logger.error(f"Batch item {i} failed: {e}")
                item.status = BatchItemStatus.FAILED
                item.error_message = str(e)

            item.completed_at = time.time()
            self._save_state()

            await self._broadcast({
                "type": "batch_item_completed",
                "index": i,
                "total": len(self._state.items),
                "match_label": item.match_label,
                "status": item.status,
                "frames_captured": item.frames_captured,
            })

            # Delay between captures (rate limiting for Footballia)
            remaining = len(self._state.items) - i - 1
            if remaining > 0 and not self._cancelled:
                delay = self._state.delay_between
                await self._broadcast({
                    "type": "batch_delay",
                    "seconds": delay,
                    "remaining": remaining,
                })
                for _ in range(delay):
                    if self._cancelled:
                        break
                    await asyncio.sleep(1)

        self._state.status = "completed" if not self._cancelled else "cancelled"
        self._save_state()

        # Summary
        completed = sum(1 for it in self._state.items if it.status == BatchItemStatus.COMPLETED)
        failed = sum(1 for it in self._state.items if it.status == BatchItemStatus.FAILED)
        total_frames = sum(it.frames_captured for it in self._state.items)
        total_cost = sum(it.api_cost for it in self._state.items)

        await self._broadcast({
            "type": "batch_completed",
            "batch_id": self._state.batch_id,
            "completed": completed,
            "failed": failed,
            "total": len(self._state.items),
            "total_frames": total_frames,
            "total_cost": total_cost,
        })

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def cancel(self):
        self._cancelled = True

    def get_state(self) -> Optional[dict]:
        if not self._state:
            return None
        return {
            "batch_id": self._state.batch_id,
            "status": self._state.status,
            "current_index": self._state.current_index,
            "total": len(self._state.items),
            "items": [asdict(item) for item in self._state.items],
        }

    def _save_state(self):
        """Persist batch state to disk for crash recovery."""
        if not self._state:
            return
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state_dict = asdict(self._state)
        self.STATE_FILE.write_text(json.dumps(state_dict, indent=2), encoding="utf-8")

    def load_state(self) -> bool:
        """Load batch state from disk. Returns True if a resumable batch exists."""
        if not self.STATE_FILE.exists():
            return False
        try:
            data = json.loads(self.STATE_FILE.read_text(encoding="utf-8"))
            items = [BatchItem(**item) for item in data.pop("items", [])]
            self._state = BatchState(**data)
            self._state.items = items
            return self._state.status == "running"
        except Exception as e:
            logger.warning(f"Failed to load batch state: {e}")
            return False

    async def _broadcast(self, msg: dict):
        if self._broadcast_fn:
            await self._broadcast_fn(msg)

    @staticmethod
    def _match_label(match: dict) -> str:
        md = match.get("match_day", match.get("md", ""))
        opp = match.get("opponent", match.get("away_team", ""))
        ha = match.get("home_away", "")
        return f"MD{md} {opp} ({ha})" if md else opp or "Unknown"
