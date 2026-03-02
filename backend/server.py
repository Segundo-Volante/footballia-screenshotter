import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.excel_manager import ExcelManager
from backend.pipeline import Pipeline
from backend.utils import load_config, logger, CAMERA_TYPES, CAMERA_DESCRIPTIONS

app = FastAPI(title="Footballia Screenshotter")

# Serve frontend static files
app.mount("/static", StaticFiles(directory="frontend"), name="static")

# Global state
active_pipeline: Pipeline | None = None
pipeline_task: asyncio.Task | None = None
ws_clients: set[WebSocket] = set()


class CaptureRequest(BaseModel):
    match_id: str
    footballia_url: str
    targets: dict[str, int]
    start_time: str = "00:00"
    match_data: dict = {}


async def broadcast(message: dict):
    dead = set()
    for ws in ws_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


@app.get("/")
async def root():
    return FileResponse("frontend/index.html")


@app.get("/api/matches")
async def get_matches():
    try:
        excel = ExcelManager("data/atletico_madrid_2425_la_liga.xlsx")
        return excel.get_all_matches()
    except FileNotFoundError:
        return []


@app.get("/api/config")
async def get_config():
    config = load_config()
    return {
        "defaults": config["defaults"],
        "sampling_interval": config["sampling"]["interval_seconds"],
        "camera_types": CAMERA_TYPES,
        "camera_descriptions": CAMERA_DESCRIPTIONS,
    }


@app.get("/api/health")
async def health_check():
    """Verify OpenAI API connectivity."""
    from backend.utils import get_openai_key
    try:
        key = get_openai_key()
        from openai import OpenAI
        client = OpenAI(api_key=key)
        models = client.models.list()
        has_model = any("gpt-4o-mini" in m.id for m in models)
        return {"status": "ok", "api_key_set": True, "model_available": has_model}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/capture/start")
async def start_capture(body: CaptureRequest):
    global active_pipeline, pipeline_task

    if active_pipeline and active_pipeline.status == "capturing":
        return {"status": "error", "message": "Capture already in progress"}

    config = load_config()

    match_data = dict(body.match_data)
    match_data["footballia_url"] = body.footballia_url
    if "md" not in match_data:
        match_data["md"] = body.match_id.replace("MD", "")

    active_pipeline = Pipeline(
        match=match_data,
        targets=body.targets,
        start_time=body.start_time,
        config=config,
        broadcast_fn=broadcast,
    )

    pipeline_task = asyncio.create_task(active_pipeline.run())
    logger.info(f"Capture started for {body.match_id}")

    return {"status": "started"}


@app.post("/api/capture/pause")
async def pause_capture():
    if active_pipeline:
        active_pipeline.pause()
        return {"status": "paused"}
    return {"status": "error", "message": "No active capture"}


@app.post("/api/capture/resume")
async def resume_capture():
    if active_pipeline:
        active_pipeline.resume()
        return {"status": "resumed"}
    return {"status": "error", "message": "No active capture"}


@app.post("/api/capture/stop")
async def stop_capture():
    if active_pipeline:
        active_pipeline.stop()
        return {"status": "stopped"}
    return {"status": "error", "message": "No active capture"}


@app.get("/api/capture/status")
async def capture_status():
    if active_pipeline:
        return {
            "status": active_pipeline.status,
            "progress": active_pipeline.get_status(),
        }
    return {"status": "idle"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    logger.info(f"WebSocket client connected ({len(ws_clients)} total)")

    try:
        # Send current status if pipeline is running
        if active_pipeline and active_pipeline.status != "idle":
            await ws.send_json(active_pipeline.get_status())
            await ws.send_json({
                "type": "status",
                "status": active_pipeline.status,
                "message": f"Pipeline is {active_pipeline.status}",
            })

        # Listen for client actions
        while True:
            data = await ws.receive_json()
            action = data.get("action", "")

            if action == "pause" and active_pipeline:
                active_pipeline.pause()
            elif action == "resume" and active_pipeline:
                active_pipeline.resume()
            elif action == "stop" and active_pipeline:
                active_pipeline.stop()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"WebSocket error: {e}")
    finally:
        ws_clients.discard(ws)
        logger.info(f"WebSocket client disconnected ({len(ws_clients)} total)")
