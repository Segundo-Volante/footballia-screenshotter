"""Tests for the consistency checker module."""

class TestConsistencyChecker:

    def test_first_frame_is_always_consistent(self):
        from backend.consistency_checker import ConsistencyChecker
        cc = ConsistencyChecker(window_size=6)
        result = cc.check("WIDE_CENTER", scene_changed=False)
        assert result["consistent"]
        assert not result["anomaly"]

    def test_scene_change_resets_window(self):
        from backend.consistency_checker import ConsistencyChecker
        cc = ConsistencyChecker(window_size=6)
        for _ in range(5):
            cc.check("WIDE_CENTER", scene_changed=False)
        # Scene change → new type is trusted
        result = cc.check("CLOSEUP", scene_changed=True)
        assert result["consistent"]
        assert not result["anomaly"]

    def test_outlier_detected(self):
        from backend.consistency_checker import ConsistencyChecker
        cc = ConsistencyChecker(window_size=6)
        # Build a strong window of WIDE_CENTER
        for _ in range(5):
            cc.check("WIDE_CENTER", scene_changed=False)
        # Now a sudden MEDIUM without scene change → anomaly
        result = cc.check("MEDIUM", scene_changed=False)
        assert not result["consistent"]
        assert result["anomaly"]
        assert result["suggested_type"] == "WIDE_CENTER"

    def test_mixed_window_no_anomaly(self):
        from backend.consistency_checker import ConsistencyChecker
        cc = ConsistencyChecker(window_size=6)
        # Mixed types — no clear majority
        cc.check("WIDE_CENTER", False)
        cc.check("MEDIUM", False)
        cc.check("WIDE_LEFT", False)
        cc.check("WIDE_CENTER", False)
        result = cc.check("CLOSEUP", scene_changed=False)
        # Not enough majority to flag an anomaly
        assert result["consistent"]

    def test_anomaly_count_tracked(self):
        from backend.consistency_checker import ConsistencyChecker
        cc = ConsistencyChecker(window_size=6)
        for _ in range(5):
            cc.check("WIDE_CENTER", False)
        cc.check("MEDIUM", False)  # anomaly
        assert cc.get_anomaly_count() == 1

    def test_reset_clears_state(self):
        from backend.consistency_checker import ConsistencyChecker
        cc = ConsistencyChecker()
        for _ in range(5):
            cc.check("WIDE_CENTER", False)
        cc.check("MEDIUM", False)  # anomaly
        cc.reset()
        assert cc.get_anomaly_count() == 0
        # After reset, first frame is trusted again
        result = cc.check("CLOSEUP", False)
        assert result["consistent"]

    def test_short_window_no_false_positives(self):
        from backend.consistency_checker import ConsistencyChecker
        cc = ConsistencyChecker(window_size=6)
        # Only 2 frames — too few to flag
        cc.check("WIDE_CENTER", False)
        cc.check("WIDE_CENTER", False)
        result = cc.check("MEDIUM", False)
        assert not result["anomaly"]
