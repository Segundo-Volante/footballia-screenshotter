"""
Cross-platform utility functions.

Handles OS-specific differences in file paths, ports, dependencies,
and browser profiles.
"""
import logging
import os
import platform
import socket
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def get_data_dir() -> Path:
    """
    Return the application data directory, cross-platform.
    Windows: %LOCALAPPDATA%/footballia-screenshotter
    macOS:   ~/Library/Application Support/footballia-screenshotter
    Linux:   ~/.local/share/footballia-screenshotter

    Falls back to ./data if platform detection fails.
    """
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:  # Linux and others
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

    data_dir = base / "footballia-screenshotter"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_browser_profile_dir() -> Path:
    """Return the Playwright browser profile directory."""
    profile_dir = get_data_dir() / "browser_profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


def find_available_port(preferred: int = 8000, range_start: int = 8000, range_end: int = 8100) -> int:
    """
    Find an available port. Returns the preferred port if available,
    otherwise scans the range.

    On macOS, AirPlay Receiver can occupy ports 5000 and 7000.
    Port 8000 is usually safe but we check anyway.
    """
    for port in [preferred] + list(range(range_start, range_end)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return port
        except OSError:
            continue
    logger.warning(f"No available port in range {range_start}-{range_end}, using {preferred}")
    return preferred


def check_dependencies() -> list[dict]:
    """
    Check all required and optional dependencies.
    Returns list of issues, each with {level, component, message, fix}.
    """
    issues = []

    # ── Required: Python version ──
    if sys.version_info < (3, 10):
        issues.append({
            "level": "error",
            "component": "python",
            "message": f"Python 3.10+ required, found {sys.version}",
            "fix": "Install Python 3.10 or later from python.org",
        })

    # ── Required: Playwright ──
    try:
        from playwright.async_api import async_playwright
        # Check if browser is installed
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=10,
        )
        # If dry-run says "already installed", we're good
    except ImportError:
        issues.append({
            "level": "error",
            "component": "playwright",
            "message": "Playwright not installed",
            "fix": "Run: pip install playwright && playwright install chromium",
        })
    except Exception:
        pass

    # ── Optional: OpenCV (only needed for Local File mode) ──
    try:
        import cv2
    except ImportError:
        issues.append({
            "level": "warning",
            "component": "opencv",
            "message": "opencv-python not installed (needed for Local File mode)",
            "fix": "Run: pip install opencv-python",
        })

    # ── Optional: Google Generative AI (only for Gemini) ──
    try:
        import google.generativeai
    except ImportError:
        issues.append({
            "level": "info",
            "component": "gemini",
            "message": "google-generativeai not installed (needed for Gemini classifier)",
            "fix": "Run: pip install google-generativeai",
        })

    # ── Linux-specific: system dependencies ──
    if platform.system() == "Linux":
        # Check for libGL (needed by OpenCV)
        import shutil
        if shutil.which("ldconfig"):
            try:
                import subprocess
                result = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, timeout=5)
                if "libGL" not in result.stdout:
                    issues.append({
                        "level": "warning",
                        "component": "libgl",
                        "message": "libGL not found (needed by OpenCV)",
                        "fix": "Run: sudo apt install libgl1-mesa-glx (Ubuntu/Debian)",
                    })
            except Exception:
                pass

    return issues


def get_platform_info() -> dict:
    """Return platform information for diagnostics and UI display."""
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "python": sys.version,
        "arch": platform.machine(),
        "drm_bypass_reliable": platform.system() in ("Windows", "Linux"),
        "drm_bypass_warning": platform.system() == "Darwin",
    }
