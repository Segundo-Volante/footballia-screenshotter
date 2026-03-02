import uvicorn

if __name__ == "__main__":
    print()
    print("  Footballia Screenshotter")
    print("  ========================")
    print("  Open http://localhost:8000 in your browser")
    print()
    uvicorn.run("backend.server:app", host="0.0.0.0", port=8000, reload=False)
