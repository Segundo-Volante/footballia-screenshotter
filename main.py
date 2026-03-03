import uvicorn
from backend.platform_utils import find_available_port, check_dependencies

if __name__ == "__main__":
    # Check dependencies on first run
    issues = check_dependencies()
    for issue in issues:
        level = issue["level"]
        msg = f"[{level.upper()}] {issue['component']}: {issue['message']}"
        if issue.get("fix"):
            msg += f"\n  Fix: {issue['fix']}"
        if level == "error":
            print(f"\033[91m{msg}\033[0m")  # Red
        elif level == "warning":
            print(f"\033[93m{msg}\033[0m")  # Yellow
        else:
            print(f"\033[90m{msg}\033[0m")  # Gray

    errors = [i for i in issues if i["level"] == "error"]
    if errors:
        print("\nCritical issues detected. Please fix them before running.")
        # Don't exit — let user proceed if they want (maybe they don't need the broken feature)

    port = find_available_port(preferred=8000)
    print(f"\n  Footballia Screenshotter starting on http://localhost:{port}\n")
    uvicorn.run("backend.server:app", host="0.0.0.0", port=port, reload=False)
