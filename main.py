import socket
import uvicorn


def find_port(start=8000, end=8100) -> int:
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start


if __name__ == "__main__":
    port = find_port()
    print()
    print("  Footballia Screenshotter")
    print("  ========================")
    print(f"  Open http://localhost:{port} in your browser")
    print()
    uvicorn.run("backend.server:app", host="0.0.0.0", port=port, reload=False)
