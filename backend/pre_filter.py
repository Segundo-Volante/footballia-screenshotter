"""
Pre-filter — local frame analysis to skip frames that don't need API classification.

Runs BEFORE the classifier. Eliminates ~30-40% of unnecessary API calls by detecting:
1. Black frames (DRM, scene transitions)
2. Duplicate/near-duplicate frames (same shot, no camera change)
3. Scene changes (camera cut — flag for priority processing)
4. Overlay detection (scoreboard/graphics present)

All operations use 64x64 thumbnails for speed. No API calls. No imports beyond Pillow/numpy.
"""
import io
import logging
import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

# Thresholds (tuned for football broadcasts)
BLACK_FRAME_THRESHOLD = 15       # Mean pixel brightness below this = black
DUPLICATE_THRESHOLD = 0.03       # Pixel difference ratio below this = duplicate
SCENE_CHANGE_THRESHOLD = 0.35   # Pixel difference ratio above this = camera cut
OVERLAY_EDGE_THRESHOLD = 30      # Edge density above this = graphics overlay present
COMPARE_SIZE = (64, 64)


class PreFilter:
    """
    Stateful pre-filter. Maintains reference to previous frame for comparison.

    analyze() returns:
    {
        "pass": bool,              # True = send to classifier
        "reason": str,             # "ok", "black_frame", "duplicate"
        "scene_change": bool,      # True = camera cut detected
        "diff_score": float,       # 0.0-1.0 difference from previous frame
        "brightness": float,       # Mean pixel brightness 0-255
        "has_overlay": bool,       # Scoreboard/graphics detected
    }
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._last_frame_small: np.ndarray | None = None
        self._stats = {
            "total": 0,
            "passed": 0,
            "black": 0,
            "duplicate": 0,
            "scene_changes": 0,
        }

    def analyze(self, jpeg_bytes: bytes) -> dict:
        self._stats["total"] += 1

        if not self.enabled:
            self._stats["passed"] += 1
            return {"pass": True, "reason": "ok", "scene_change": False,
                    "diff_score": 0.0, "brightness": 128.0, "has_overlay": False}

        try:
            img = Image.open(io.BytesIO(jpeg_bytes))
            small = img.resize(COMPARE_SIZE)
            pixels = np.array(small, dtype=np.float32)

            result = {
                "pass": True,
                "reason": "ok",
                "scene_change": False,
                "diff_score": 0.0,
                "brightness": 0.0,
                "has_overlay": False,
            }

            # ── Filter 1: Black frame detection ──
            brightness = float(pixels.mean())
            result["brightness"] = brightness
            if brightness < BLACK_FRAME_THRESHOLD:
                result["pass"] = False
                result["reason"] = "black_frame"
                self._stats["black"] += 1
                return result

            # ── Filter 2: Duplicate frame detection ──
            if self._last_frame_small is not None:
                diff = float(np.abs(pixels - self._last_frame_small).mean() / 255.0)
                result["diff_score"] = diff

                if diff < DUPLICATE_THRESHOLD:
                    result["pass"] = False
                    result["reason"] = "duplicate"
                    self._stats["duplicate"] += 1
                    # Don't update _last_frame_small — keep comparing against the
                    # older reference so we detect change from the original shot
                    return result

                if diff > SCENE_CHANGE_THRESHOLD:
                    result["scene_change"] = True
                    self._stats["scene_changes"] += 1

            # ── Filter 3: Overlay detection (simplified) ──
            # Check top 12% and bottom 12% for high edge density (scoreboards, graphics)
            h = img.height
            top_strip = img.crop((0, 0, img.width, int(h * 0.12)))
            bot_strip = img.crop((0, int(h * 0.88), img.width, h))
            top_edges = np.array(top_strip.filter(ImageFilter.FIND_EDGES)).mean()
            bot_edges = np.array(bot_strip.filter(ImageFilter.FIND_EDGES)).mean()
            result["has_overlay"] = bool(
                top_edges > OVERLAY_EDGE_THRESHOLD or bot_edges > OVERLAY_EDGE_THRESHOLD
            )

            # Update reference frame (only when frame passes)
            self._last_frame_small = pixels
            self._stats["passed"] += 1
            return result

        except Exception as e:
            logger.warning(f"PreFilter error: {e}")
            self._stats["passed"] += 1
            return {"pass": True, "reason": "error_passthrough", "scene_change": False,
                    "diff_score": 0.0, "brightness": 128.0, "has_overlay": False}

    def get_stats(self) -> dict:
        return dict(self._stats)

    def reset(self):
        self._last_frame_small = None
        self._stats = {"total": 0, "passed": 0, "black": 0, "duplicate": 0, "scene_changes": 0}
