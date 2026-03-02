"""
Adaptive Sampler — dynamically adjusts screenshot interval.

Instead of fixed 2-second intervals, adapts based on:
1. Scene changes -> fast capture (1s) to catch new camera angles
2. Same category, target already met -> slow capture (5-6s) to save API calls
3. Only rare categories remain -> normal interval, accept that most frames won't match
4. Normal conditions -> base interval (2s)

Saves ~20-30% additional API calls on top of pre-filter savings.
"""
from collections import Counter


class AdaptiveSampler:

    def __init__(
        self,
        base_interval: float = 2.0,
        min_interval: float = 1.0,
        max_interval: float = 6.0,
        enabled: bool = True,
    ):
        self.base = base_interval
        self.min = min_interval
        self.max = max_interval
        self.enabled = enabled
        self._current = base_interval
        self._recent_types: list[str] = []
        self._scene_change_cooldown = 0
        self._window_size = 8

    def get_interval(
        self,
        last_classification: dict | None,
        pre_filter_result: dict,
        targets_status: dict[str, bool],
    ) -> float:
        """
        Calculate how long to wait before next screenshot.

        Args:
            last_classification: Result from classifier (or None if frame was filtered)
            pre_filter_result: Result from PreFilter.analyze()
            targets_status: {category: True/False} where True = target met

        Returns:
            Interval in seconds.
        """
        if not self.enabled:
            return self.base

        # ── Case 1: Scene change detected -> capture quickly ──
        if pre_filter_result.get("scene_change"):
            self._scene_change_cooldown = 3
            return self.min

        if self._scene_change_cooldown > 0:
            self._scene_change_cooldown -= 1
            return self.min

        # ── Case 2: Recent frames are same type and target is met ──
        if last_classification and not last_classification.get("is_pending"):
            cam_type = last_classification.get("classified_as", "")
            if cam_type and cam_type != "PENDING":
                self._recent_types.append(cam_type)
                if len(self._recent_types) > self._window_size:
                    self._recent_types.pop(0)

                if len(self._recent_types) >= 4:
                    counts = Counter(self._recent_types[-5:])
                    most_common_type, count = counts.most_common(1)[0]
                    if count >= 4 and targets_status.get(most_common_type, False):
                        return self.max

        # ── Case 3: Only rare categories remain ──
        unfilled = [t for t, met in targets_status.items() if not met and t not in ("OTHER", "PENDING")]
        if unfilled:
            rare_types = {"BEHIND_GOAL", "AERIAL", "PENALTY", "CARD_SHOWN", "KICKOFF"}
            if all(t in rare_types for t in unfilled):
                return self.base  # Don't slow down — rare angles need patience

        return self.base

    def reset(self):
        self._recent_types.clear()
        self._scene_change_cooldown = 0
