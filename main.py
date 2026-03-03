#!/usr/bin/env python3
"""
Footballia Screenshotter — main entry point.
Runs the FastAPI server with all startup checks.
"""
import sys
import logging
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from backend.platform_utils import (
    find_available_port,
    check_dependencies,
    get_platform_info,
)


def main():
    # ── Dependency check ──
    issues = check_dependencies()
    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]
    infos = [i for i in issues if i["level"] == "info"]

    if errors:
        print("\n❌ Missing required dependencies:")
        for e in errors:
            print(f"   {e['component']}: {e['message']}")
            print(f"   Fix: {e['fix']}")
        print()
        sys.exit(1)

    if warnings:
        print("\n⚠️  Optional dependencies missing:")
        for w in warnings:
            print(f"   {w['component']}: {w['message']}")
        print()

    if infos:
        for i in infos:
            print(f"   ℹ️  {i['component']}: {i['message']}")

    # ── Platform info ──
    info = get_platform_info()
    if info.get("drm_bypass_warning"):
        print(f"\n⚠️  {info['drm_bypass_warning']}")

    # ── Check for interrupted captures ──
    try:
        from backend.server import check_incomplete_captures
        interrupted = check_incomplete_captures()
        if interrupted:
            print(f"\n⚠️  Found {interrupted} interrupted capture(s) from previous session.")
            print("   You can resume them from the Dashboard.")
    except Exception:
        pass

    # ── Find available port ──
    port = find_available_port(preferred=8000)

    # ── Start server ──
    print(f"\n⚽ Footballia Screenshotter starting on http://localhost:{port}")
    print(f"   Platform: {info.get('os', 'unknown')} ({info.get('arch', '')})")
    print(f"   Python: {info.get('python', '')}")
    print()

    import uvicorn
    uvicorn.run(
        "backend.server:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
