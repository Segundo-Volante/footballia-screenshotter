"""Tests for the pre-filter module."""
import numpy as np
from io import BytesIO
from PIL import Image


def _make_frame(brightness=128, width=64, height=64):
    """Create a synthetic JPEG frame of given brightness."""
    arr = np.full((height, width, 3), brightness, dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestPreFilter:

    def test_black_frame_detected(self):
        from backend.pre_filter import PreFilter
        pf = PreFilter(enabled=True)
        frame = _make_frame(brightness=5)
        result = pf.analyze(frame)
        assert not result["pass"]
        assert "black" in result["reason"].lower()

    def test_normal_frame_passes(self):
        from backend.pre_filter import PreFilter
        pf = PreFilter(enabled=True)
        frame = _make_frame(brightness=128)
        result = pf.analyze(frame)
        assert result["pass"]

    def test_duplicate_frame_detected(self):
        from backend.pre_filter import PreFilter
        pf = PreFilter(enabled=True)
        frame = _make_frame(brightness=128)
        r1 = pf.analyze(frame)
        assert r1["pass"]
        r2 = pf.analyze(frame)  # Same frame again
        assert not r2["pass"]
        assert "duplicate" in r2["reason"].lower()

    def test_scene_change_detected(self):
        from backend.pre_filter import PreFilter
        pf = PreFilter(enabled=True)
        frame1 = _make_frame(brightness=50)
        frame2 = _make_frame(brightness=200)
        pf.analyze(frame1)
        result = pf.analyze(frame2)
        assert result["pass"]
        assert result["scene_change"]

    def test_disabled_filter_passes_everything(self):
        from backend.pre_filter import PreFilter
        pf = PreFilter(enabled=False)
        frame = _make_frame(brightness=5)
        result = pf.analyze(frame)
        assert result["pass"]

    def test_stats_tracking(self):
        from backend.pre_filter import PreFilter
        pf = PreFilter(enabled=True)
        pf.analyze(_make_frame(5))       # black
        pf.analyze(_make_frame(128))     # pass
        pf.analyze(_make_frame(128))     # duplicate
        stats = pf.get_stats()
        assert stats["total"] == 3
        assert stats["passed"] == 1
        assert stats["black"] == 1
        assert stats["duplicate"] == 1
