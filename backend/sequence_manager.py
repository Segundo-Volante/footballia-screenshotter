"""
Sequence Capture System — FSM-based multi-trigger sequence capture.

Each sequence profile (wide, medium, closeup) has an independent FSM:
  IDLE → ARMED → CAPTURING → COOLDOWN → IDLE

The SequenceDispatcher routes classifier results to all FSMs and
enforces mutual exclusion (only one CAPTURING at a time via preemption).
"""

import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

from backend.utils import logger


class FSMState(str, Enum):
    IDLE = "IDLE"
    ARMED = "ARMED"
    CAPTURING = "CAPTURING"
    COOLDOWN = "COOLDOWN"


@dataclass
class SequenceRecord:
    """Metadata for a single captured sequence."""
    sequence_id: str
    profile_name: str
    trigger_angle: str
    start_time: float      # wall-clock
    start_video_time: float
    end_video_time: Optional[float] = None
    frame_count: int = 0
    truncated: bool = False
    preempted: bool = False
    preempted_by: Optional[str] = None  # profile name that preempted this sequence


@dataclass
class ProfileFSM:
    """Independent state machine for one sequence profile."""
    name: str               # e.g. "wide_annotation"
    short_name: str          # e.g. "wide" — used in sequence IDs
    triggers: list[str]      # camera angles that trigger this profile
    duration_sec: float
    interval_sec: float
    tolerance_sec: float
    cooldown_sec: float
    purpose: str
    skip_classifier_during_capture: bool
    enabled: bool = True

    # Runtime state
    state: FSMState = FSMState.IDLE
    armed_angle: Optional[str] = None         # angle that caused ARMED transition
    capture_start_wall: float = 0.0           # wall-clock when CAPTURING started
    capture_start_video: float = 0.0          # video time when CAPTURING started
    last_capture_wall: float = 0.0            # wall-clock of last frame capture
    cooldown_start_wall: float = 0.0          # wall-clock when COOLDOWN started
    tolerance_start_wall: Optional[float] = None  # wall-clock when tolerance timer began
    current_sequence_id: Optional[str] = None
    current_trigger_angle: Optional[str] = None
    frame_count: int = 0
    last_frame_video_time: float = 0.0        # video time of most recent frame
    sequence_counter: int = 0                 # per-profile counter across session

    # Stall detection
    _last_pixel_hash: Optional[int] = None
    _consecutive_identical: int = 0
    _stall_paused: bool = False

    def matches_trigger(self, camera_angle: str) -> bool:
        return camera_angle in self.triggers

    def elapsed_capturing(self) -> float:
        if self.state != FSMState.CAPTURING:
            return 0.0
        return time.time() - self.capture_start_wall

    def remaining_cooldown(self) -> float:
        if self.state != FSMState.COOLDOWN:
            return 0.0
        elapsed = time.time() - self.cooldown_start_wall
        return max(0.0, self.cooldown_sec - elapsed)

    def should_capture_now(self) -> bool:
        """Check if enough time has passed since last frame capture."""
        if self.state != FSMState.CAPTURING:
            return False
        if self._stall_paused:
            return False
        return (time.time() - self.last_capture_wall) >= self.interval_sec

    def duration_expired(self) -> bool:
        if self.state != FSMState.CAPTURING:
            return False
        return self.elapsed_capturing() >= self.duration_sec

    def generate_sequence_id(self) -> str:
        self.sequence_counter += 1
        return f"seq_{self.short_name}_{self.sequence_counter:03d}"


class SequenceDispatcher:
    """
    Routes classifier outputs to all profile FSMs.
    Enforces mutual exclusion: only one FSM can be CAPTURING at a time.
    """

    def __init__(self, profiles_config: dict):
        self.profiles: dict[str, ProfileFSM] = {}
        self._sequences: list[SequenceRecord] = []
        self._session_counts: dict[str, dict] = {}  # {short_name: {count, frames}}
        self._pending_backfills: list[SequenceRecord] = []
        self._build_profiles(profiles_config)

    def _build_profiles(self, config: dict):
        short_names = {
            "wide_annotation": "wide",
            "medium_reid": "med",
            "closeup_reid": "close",
        }
        for name, prof in config.items():
            short = short_names.get(name, name[:5])
            self.profiles[name] = ProfileFSM(
                name=name,
                short_name=short,
                triggers=prof.get("trigger", []),
                duration_sec=prof.get("duration_sec", 5),
                interval_sec=prof.get("interval_sec", 1.0),
                tolerance_sec=prof.get("tolerance_sec", 1.0),
                cooldown_sec=prof.get("cooldown_sec", 10),
                purpose=prof.get("purpose", ""),
                skip_classifier_during_capture=prof.get("skip_classifier_during_capture", True),
                enabled=prof.get("enabled", True),
            )
            self._session_counts[short] = {"count": 0, "frames": 0}

    def update_profiles(self, new_config: dict):
        """Update profile parameters from frontend. Preserves runtime state where possible."""
        for name, prof_config in new_config.items():
            if name in self.profiles:
                fsm = self.profiles[name]
                fsm.triggers = prof_config.get("trigger", fsm.triggers)
                fsm.duration_sec = prof_config.get("duration_sec", fsm.duration_sec)
                fsm.interval_sec = prof_config.get("interval_sec", fsm.interval_sec)
                fsm.tolerance_sec = prof_config.get("tolerance_sec", fsm.tolerance_sec)
                fsm.cooldown_sec = prof_config.get("cooldown_sec", fsm.cooldown_sec)
                fsm.purpose = prof_config.get("purpose", fsm.purpose)
                fsm.skip_classifier_during_capture = prof_config.get(
                    "skip_classifier_during_capture", fsm.skip_classifier_during_capture
                )
                fsm.enabled = prof_config.get("enabled", fsm.enabled)

    def get_profiles_config(self) -> dict:
        """Return current profile configs for API response."""
        result = {}
        for name, fsm in self.profiles.items():
            result[name] = {
                "enabled": fsm.enabled,
                "trigger": fsm.triggers,
                "duration_sec": fsm.duration_sec,
                "interval_sec": fsm.interval_sec,
                "tolerance_sec": fsm.tolerance_sec,
                "cooldown_sec": fsm.cooldown_sec,
                "purpose": fsm.purpose,
                "skip_classifier_during_capture": fsm.skip_classifier_during_capture,
            }
        return result

    def get_all_status(self) -> dict:
        """Return current FSM states for all profiles."""
        result = {}
        for name, fsm in self.profiles.items():
            status = {"state": fsm.state.value, "enabled": fsm.enabled}
            if fsm.state == FSMState.CAPTURING:
                status["sequence_id"] = fsm.current_sequence_id
                status["elapsed"] = round(fsm.elapsed_capturing(), 1)
                status["frames"] = fsm.frame_count
                status["duration_sec"] = fsm.duration_sec
            elif fsm.state == FSMState.COOLDOWN:
                status["remaining"] = round(fsm.remaining_cooldown(), 1)
            result[name] = status
        return result

    def get_session_summary(self) -> dict:
        """Return session totals per profile type."""
        return dict(self._session_counts)

    def pop_pending_backfills(self) -> list[SequenceRecord]:
        """Return and clear all sequence records awaiting backfill."""
        records = self._pending_backfills
        self._pending_backfills = []
        return records

    def get_capturing_profile(self) -> Optional[ProfileFSM]:
        """Return the FSM currently in CAPTURING state, if any."""
        for fsm in self.profiles.values():
            if fsm.state == FSMState.CAPTURING and fsm.enabled:
                return fsm
        return None

    def has_any_enabled(self) -> bool:
        return any(fsm.enabled for fsm in self.profiles.values())

    def on_classifier_result(self, camera_angle: str, video_time: float) -> list[dict]:
        """
        Process a classifier result. Returns a list of events to broadcast.

        Events:
          {"type": "sequence_state", "profile": ..., "state": ..., ...}
          {"type": "sequence_summary", ...}
        """
        events = []
        now = time.time()

        # First, check cooldown expirations
        for fsm in self.profiles.values():
            if fsm.state == FSMState.COOLDOWN:
                if fsm.remaining_cooldown() <= 0:
                    fsm.state = FSMState.IDLE
                    events.append(self._state_event(fsm))

        # Find which profiles match this camera_angle
        matching_profiles = [
            fsm for fsm in self.profiles.values()
            if fsm.enabled and fsm.matches_trigger(camera_angle)
        ]
        non_matching_profiles = [
            fsm for fsm in self.profiles.values()
            if fsm.enabled and not fsm.matches_trigger(camera_angle)
        ]

        currently_capturing = self.get_capturing_profile()

        # === Handle PREEMPTION ===
        # If something is CAPTURING and a DIFFERENT profile matches, preempt
        if currently_capturing:
            other_matches = [
                fsm for fsm in matching_profiles
                if fsm.name != currently_capturing.name
            ]
            if other_matches and not currently_capturing.matches_trigger(camera_angle):
                # Preempt current capture — record which profile caused preemption
                preempting_name = other_matches[0].name
                self._end_sequence(
                    currently_capturing, preempted=True, preempted_by=preempting_name
                )
                currently_capturing.state = FSMState.IDLE  # No cooldown on preemption
                events.append(self._state_event(currently_capturing))
                logger.info(
                    f"Sequence preempted: {currently_capturing.name} by {preempting_name}"
                )
                currently_capturing = None

        # === Process each matching profile ===
        for fsm in matching_profiles:
            if fsm.state == FSMState.IDLE:
                # Transition to ARMED
                fsm.state = FSMState.ARMED
                fsm.armed_angle = camera_angle
                events.append(self._state_event(fsm))
                logger.info(f"Sequence ARMED: {fsm.name} triggered by {camera_angle}")

            elif fsm.state == FSMState.ARMED:
                # Confirmation: second consecutive match → CAPTURING
                # But only if no other profile is already CAPTURING
                if currently_capturing is None:
                    fsm.state = FSMState.CAPTURING
                    fsm.capture_start_wall = now
                    fsm.capture_start_video = video_time
                    fsm.last_capture_wall = 0.0  # Force immediate first capture
                    fsm.frame_count = 0
                    fsm.current_sequence_id = fsm.generate_sequence_id()
                    fsm.current_trigger_angle = camera_angle
                    fsm.tolerance_start_wall = None
                    fsm._stall_paused = False
                    fsm._consecutive_identical = 0

                    currently_capturing = fsm
                    events.append(self._state_event(fsm))
                    logger.info(
                        f"Sequence CAPTURING: {fsm.name} ({fsm.current_sequence_id})"
                    )
                else:
                    # Can't start — another is capturing. Stay ARMED.
                    pass

            elif fsm.state == FSMState.CAPTURING:
                # Same profile still matches — reset tolerance timer
                fsm.tolerance_start_wall = None

            # COOLDOWN state: ignore triggers (by design)

        # === Handle tolerance for CAPTURING profile with classifier running ===
        if currently_capturing and not currently_capturing.skip_classifier_during_capture:
            if not currently_capturing.matches_trigger(camera_angle):
                # Check if it matches any OTHER trigger (preemption handled above)
                other_trigger = any(
                    fsm.matches_trigger(camera_angle)
                    for fsm in self.profiles.values()
                    if fsm.enabled and fsm.name != currently_capturing.name
                )
                if not other_trigger:
                    # Start or continue tolerance timer
                    if currently_capturing.tolerance_start_wall is None:
                        currently_capturing.tolerance_start_wall = now
                    elif (now - currently_capturing.tolerance_start_wall) >= currently_capturing.tolerance_sec:
                        # Tolerance exceeded — end normally
                        self._end_sequence(currently_capturing, preempted=False)
                        currently_capturing.state = FSMState.COOLDOWN
                        currently_capturing.cooldown_start_wall = now
                        events.append(self._state_event(currently_capturing))
                        logger.info(
                            f"Sequence ended (tolerance): {currently_capturing.name}"
                        )

        # === Reset non-matching ARMED profiles back to IDLE ===
        for fsm in non_matching_profiles:
            if fsm.state == FSMState.ARMED:
                fsm.state = FSMState.IDLE
                fsm.armed_angle = None
                events.append(self._state_event(fsm))

        # === Check duration expiry on capturing profile ===
        cap = self.get_capturing_profile()
        if cap and cap.duration_expired():
            self._end_sequence(cap, preempted=False)
            cap.state = FSMState.COOLDOWN
            cap.cooldown_start_wall = now
            events.append(self._state_event(cap))
            logger.info(f"Sequence completed (duration): {cap.name}")

        if events:
            events.append(self._summary_event())

        return events

    def tick(self) -> list[dict]:
        """
        Called periodically (e.g. every broadcast cycle) to check
        time-based transitions that don't need classifier input.
        Returns events if state changed.
        """
        events = []
        now = time.time()

        for fsm in self.profiles.values():
            if not fsm.enabled:
                continue

            # Check cooldown expiry
            if fsm.state == FSMState.COOLDOWN and fsm.remaining_cooldown() <= 0:
                fsm.state = FSMState.IDLE
                events.append(self._state_event(fsm))

            # Check duration expiry for skip_classifier profiles
            if fsm.state == FSMState.CAPTURING and fsm.duration_expired():
                self._end_sequence(fsm, preempted=False)
                fsm.state = FSMState.COOLDOWN
                fsm.cooldown_start_wall = now
                events.append(self._state_event(fsm))
                logger.info(f"Sequence completed (duration/tick): {fsm.name}")

        if events:
            events.append(self._summary_event())

        return events

    def on_frame_captured(self, profile_name: str, video_time: float = 0.0):
        """Record that a sequence frame was captured."""
        fsm = self.profiles.get(profile_name)
        if fsm and fsm.state == FSMState.CAPTURING:
            fsm.frame_count += 1
            fsm.last_capture_wall = time.time()
            fsm.last_frame_video_time = video_time

    def on_video_ended(self) -> list[dict]:
        """Handle video ending during active sequences."""
        events = []
        cap = self.get_capturing_profile()
        if cap:
            self._end_sequence(cap, preempted=False, truncated=True)
            cap.state = FSMState.IDLE
            events.append(self._state_event(cap))
            events.append(self._summary_event())
        return events

    def check_stall(self, pixel_hash: int, profile_name: str) -> bool:
        """
        Detect player/browser stall via pixel hash comparison.
        Returns True if stall detected (3+ consecutive identical frames).
        """
        fsm = self.profiles.get(profile_name)
        if not fsm or fsm.state != FSMState.CAPTURING:
            return False

        if fsm._last_pixel_hash == pixel_hash:
            fsm._consecutive_identical += 1
        else:
            fsm._consecutive_identical = 0
            fsm._stall_paused = False

        fsm._last_pixel_hash = pixel_hash

        if fsm._consecutive_identical >= 3:
            if not fsm._stall_paused:
                logger.warning(f"Stall detected in sequence {fsm.current_sequence_id}")
                fsm._stall_paused = True
            return True

        return False

    def check_green_ratio(self, jpeg_bytes: bytes) -> bool:
        """
        Lightweight check for non-game frame when skip_classifier_during_capture is True.
        Returns True if frame appears to be a game frame (green ratio >= 5%).
        """
        try:
            from PIL import Image
            import io
            import numpy as np

            img = Image.open(io.BytesIO(jpeg_bytes))
            # Downsample for speed
            img = img.resize((160, 90), Image.NEAREST)
            arr = np.array(img)

            # Green channel significantly higher than red and blue
            r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
            green_mask = (g > 60) & (g > r * 1.2) & (g > b * 1.2)
            ratio = green_mask.sum() / green_mask.size

            return ratio >= 0.05
        except Exception:
            return True  # Fail-safe: assume game frame

    def _end_sequence(
        self, fsm: ProfileFSM, preempted: bool = False, truncated: bool = False,
        preempted_by: Optional[str] = None,
    ):
        """Finalize a sequence and record it."""
        record = SequenceRecord(
            sequence_id=fsm.current_sequence_id or "unknown",
            profile_name=fsm.name,
            trigger_angle=fsm.current_trigger_angle or "",
            start_time=fsm.capture_start_wall,
            start_video_time=fsm.capture_start_video,
            end_video_time=fsm.last_frame_video_time if fsm.frame_count > 0 else fsm.capture_start_video,
            frame_count=fsm.frame_count,
            preempted=preempted,
            truncated=truncated,
            preempted_by=preempted_by,
        )
        self._sequences.append(record)

        # Queue for backfill by OutputManager
        self._pending_backfills.append(record)

        # Update session counts
        short = fsm.short_name
        if short not in self._session_counts:
            self._session_counts[short] = {"count": 0, "frames": 0}
        self._session_counts[short]["count"] += 1
        self._session_counts[short]["frames"] += fsm.frame_count

        logger.info(
            f"Sequence ended: {record.sequence_id} — {record.frame_count} frames"
            f"{' (preempted)' if preempted else ''}"
            f"{' (truncated)' if truncated else ''}"
        )

    def _state_event(self, fsm: ProfileFSM) -> dict:
        """Build a sequence_state WebSocket event."""
        event = {
            "type": "sequence_state",
            "profile": fsm.name,
            "state": fsm.state.value,
        }
        if fsm.state == FSMState.CAPTURING:
            event["sequence_id"] = fsm.current_sequence_id
            event["elapsed"] = round(fsm.elapsed_capturing(), 1)
            event["frames"] = fsm.frame_count
            event["duration_sec"] = fsm.duration_sec
        elif fsm.state == FSMState.COOLDOWN:
            event["remaining"] = round(fsm.remaining_cooldown(), 1)
        return event

    def _summary_event(self) -> dict:
        """Build a sequence_summary WebSocket event."""
        summary = {}
        for name, fsm in self.profiles.items():
            short = fsm.short_name
            counts = self._session_counts.get(short, {"count": 0, "frames": 0})
            summary[short] = {"count": counts["count"], "frames": counts["frames"]}
        return {"type": "sequence_summary", **summary}

    def get_sequence_metadata(self, profile_name: str) -> Optional[dict]:
        """
        Get per-frame metadata for the currently active sequence.
        Returns the full schema fields needed by OutputManager.
        """
        fsm = self.profiles.get(profile_name)
        if not fsm or fsm.state != FSMState.CAPTURING:
            return None
        return {
            "camera_angle_source": "trigger" if fsm.skip_classifier_during_capture else "classifier",
            "sequence_id": fsm.current_sequence_id,
            "sequence_type": fsm.name,
            "sequence_purpose": fsm.purpose,
            "sequence_position": fsm.frame_count,  # 0-indexed, incremented after save
            "sequence_total_frames": None,          # backfilled after sequence ends
            "sequence_video_time_start": fsm.capture_start_video,
            "sequence_video_time_end": None,        # backfilled after sequence ends
            "sequence_truncated": False,
            "sequence_preempted_by": None,
            "is_resample": False,
            "resample_of": None,
            "resample_original_interval": None,
        }

    def get_completed_sequences(self) -> list[dict]:
        """
        Return all completed sequence records as dicts.
        Used by OutputManager for frame_metadata.json and summary.json.
        """
        records = []
        for rec in self._sequences:
            records.append({
                "sequence_id": rec.sequence_id,
                "profile_name": rec.profile_name,
                "trigger_angle": rec.trigger_angle,
                "start_video_time": rec.start_video_time,
                "end_video_time": rec.end_video_time,
                "frame_count": rec.frame_count,
                "truncated": rec.truncated,
                "preempted": rec.preempted,
                "preempted_by": rec.preempted_by,
            })
        return records
