"""Tests for the adaptive sampling module."""


class TestAdaptiveSampler:

    def test_returns_base_interval_by_default(self):
        from backend.adaptive_sampler import AdaptiveSampler
        s = AdaptiveSampler(base_interval=2.0)
        interval = s.get_interval(None, None, {})
        assert interval == 2.0

    def test_shorter_interval_after_scene_change(self):
        from backend.adaptive_sampler import AdaptiveSampler
        s = AdaptiveSampler(base_interval=2.0, min_interval=1.0)
        pf_result = {"scene_change": True}
        interval = s.get_interval(None, pf_result, {})
        assert interval < 2.0

    def test_longer_interval_when_target_met(self):
        from backend.adaptive_sampler import AdaptiveSampler
        s = AdaptiveSampler(base_interval=2.0, max_interval=6.0)
        targets_status = {"WIDE_CENTER": True}
        classification = {"classified_as": "WIDE_CENTER"}
        # Feed several same-type classifications
        for _ in range(5):
            interval = s.get_interval(classification, {}, targets_status)
        assert interval > 2.0
