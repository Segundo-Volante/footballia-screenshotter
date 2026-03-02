"""
Consistency Checker — flags frames that are likely misclassified.

In broadcast footage, camera shots typically last 3-8 seconds.
If 4 consecutive frames are classified as WIDE_CENTER and then one is classified
as MEDIUM with no scene change detected, that MEDIUM is probably wrong.

Does NOT override the classifier's result. Just flags it as anomalous so the
Review UI can highlight it for human verification.
"""
from collections import Counter


class ConsistencyChecker:

    def __init__(self, window_size: int = 6):
        self._window_size = window_size
        self._recent: list[dict] = []  # [{type: str, scene_change: bool}]
        self._anomaly_count = 0

    def check(self, classified_as: str, scene_changed: bool) -> dict:
        """
        Check if the new classification is consistent with recent history.

        Returns:
        {
            "consistent": bool,
            "anomaly": bool,
            "suggested_type": str or None,
            "note": str,
        }
        """
        entry = {"type": classified_as, "scene_change": scene_changed}

        # Scene change resets the window — new shot, new classification is trusted
        if scene_changed:
            self._recent.clear()
            self._recent.append(entry)
            return {"consistent": True, "anomaly": False, "suggested_type": None, "note": ""}

        self._recent.append(entry)
        if len(self._recent) > self._window_size:
            self._recent.pop(0)

        if len(self._recent) < 4:
            return {"consistent": True, "anomaly": False, "suggested_type": None, "note": ""}

        # Check: is the newest classification an outlier?
        previous_types = [r["type"] for r in self._recent[:-1]]
        counts = Counter(previous_types)
        majority_type, majority_count = counts.most_common(1)[0]

        # If the majority is strong (>= N-2 of previous frames) and new frame differs
        threshold = max(2, len(previous_types) - 2)
        if classified_as != majority_type and majority_count >= threshold:
            self._anomaly_count += 1
            return {
                "consistent": False,
                "anomaly": True,
                "suggested_type": majority_type,
                "note": f"Last {majority_count} frames were {majority_type}; "
                        f"this frame classified as {classified_as} without scene change",
            }

        return {"consistent": True, "anomaly": False, "suggested_type": None, "note": ""}

    def get_anomaly_count(self) -> int:
        return self._anomaly_count

    def reset(self):
        self._recent.clear()
        self._anomaly_count = 0
