import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.match_db import MatchDB
from backend.pipeline import Pipeline
from backend.project_config import ProjectConfig
from backend.utils import load_config, logger, CAMERA_TYPES, CAMERA_DESCRIPTIONS

app = FastAPI(title="Footballia Screenshotter")

app.mount("/static", StaticFiles(directory="frontend"), name="static")

# Global state
active_pipeline: Pipeline | None = None
pipeline_task: asyncio.Task | None = None
ws_clients: set[WebSocket] = set()


# ── Pydantic models ──

class SetupRequest(BaseModel):
    team_name: str
    season: str
    competitions: list[str] = []
    language: str = "en"

class AddMatchRequest(BaseModel):
    match_day: int = 0
    date: str = ""
    home_away: str = ""
    opponent: str = ""
    score: str = ""
    result: str = ""
    competition: str = ""
    season: str = ""
    team_name: str = ""
    footballia_url: str = ""

class ImportExcelRequest(BaseModel):
    filepath: str
    competition: str = ""

class CaptureRequest(BaseModel):
    match_id: int | None = None
    footballia_url: str
    targets: dict[str, int]
    start_time: str = "00:00"
    match_data: dict = {}
    source_type: str = "footballia"


async def broadcast(message: dict):
    dead = set()
    for ws in ws_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


# ── Static routes ──

@app.get("/")
async def root():
    return FileResponse("frontend/index.html")


# ── Project config routes ──

@app.get("/api/project")
async def get_project():
    pc = ProjectConfig()
    if not pc.exists:
        return {"needs_setup": True}
    return {"needs_setup": False, "project": pc.to_dict()}


@app.post("/api/setup")
async def save_setup(body: SetupRequest):
    pc = ProjectConfig()
    pc.save(
        team_name=body.team_name,
        season=body.season,
        competitions=body.competitions,
        language=body.language,
    )
    return {"status": "ok"}


# ── Match CRUD routes ──

@app.get("/api/matches")
async def get_matches():
    db = MatchDB()
    matches = db.get_all_matches()
    db.close()
    return matches


@app.post("/api/matches")
async def add_match(body: AddMatchRequest):
    db = MatchDB()
    match_id = db.add_match(
        match_day=body.match_day,
        date=body.date,
        home_away=body.home_away,
        opponent=body.opponent,
        score=body.score or "",
        result=body.result or "",
        competition=body.competition or "",
        season=body.season or "",
        team_name=body.team_name or "",
        footballia_url=body.footballia_url or "",
    )
    db.close()
    return {"status": "ok", "id": match_id}


@app.put("/api/matches/{match_id}")
async def update_match(match_id: int, body: dict):
    db = MatchDB()
    db.update_match(match_id, **body)
    db.close()
    return {"status": "ok"}


@app.delete("/api/matches/{match_id}")
async def delete_match(match_id: int):
    db = MatchDB()
    db.delete_match(match_id)
    db.close()
    return {"status": "ok"}


@app.post("/api/matches/import-excel")
async def import_excel(body: ImportExcelRequest):
    db = MatchDB()
    pc = ProjectConfig()
    count = db.import_from_excel(
        excel_path=body.filepath,
        team_name=pc.team_name,
        season=pc.season,
        competition=body.competition or (pc.competitions[0] if pc.competitions else ""),
    )
    db.close()
    return {"status": "ok", "imported": count}


# ── Config route ──

@app.get("/api/config")
async def get_config():
    config = load_config()
    return {
        "defaults": config["defaults"],
        "sampling_interval": config["sampling"]["interval_seconds"],
        "camera_types": CAMERA_TYPES,
        "camera_descriptions": CAMERA_DESCRIPTIONS,
    }


# ── Capture routes ──

@app.post("/api/capture/start")
async def start_capture(body: CaptureRequest):
    global active_pipeline, pipeline_task

    if active_pipeline and active_pipeline.status == "capturing":
        return {"status": "error", "message": "Capture already in progress"}

    config = load_config()

    match_data = dict(body.match_data) if body.match_data else {}
    match_data["footballia_url"] = body.footballia_url

    source_type = body.source_type or "footballia"

    if source_type == "footballia":
        from backend.sources.footballia import FootballiaSource
        source = FootballiaSource(config["browser"])
        success = await source.setup(
            url=body.footballia_url,
            broadcast_fn=broadcast,
        )
        if not success:
            return {"status": "error", "message": "Failed to connect to video. Check the URL and try again."}
    else:
        return {"status": "error", "message": f"Source type '{source_type}' not yet supported"}

    # Create capture record in database
    capture_id = None
    if body.match_id:
        try:
            db = MatchDB()
            capture_id = db.create_capture(
                match_id=body.match_id,
                provider="openai",
                source_type=source_type,
                config={"targets": body.targets, "start_time": body.start_time},
            )
            db.close()
        except Exception as e:
            logger.warning(f"Failed to create capture record: {e}")

    active_pipeline = Pipeline(
        source=source,
        match=match_data,
        targets=body.targets,
        start_time=body.start_time,
        config=config,
        broadcast_fn=broadcast,
        capture_id=capture_id,
    )

    pipeline_task = asyncio.create_task(active_pipeline.run())
    logger.info(f"Capture started for {body.match_data.get('opponent', 'unknown')}")

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


# ── Health check ──

@app.get("/api/health")
async def health_check():
    from backend.utils import get_openai_key
    key = get_openai_key()
    result = {"api_key_set": bool(key and key != "sk-your-key-here")}
    if result["api_key_set"]:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key)
            models = client.models.list()
            result["model_available"] = any("gpt-4o-mini" in m.id for m in models)
            result["status"] = "ok"
        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)
    else:
        result["status"] = "no_key"
        result["message"] = "No OpenAI API key configured."
    return result


# ── WebSocket ──

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    logger.info(f"WebSocket client connected ({len(ws_clients)} total)")

    try:
        if active_pipeline and active_pipeline.status != "idle":
            await ws.send_json(active_pipeline.get_status())
            await ws.send_json({
                "type": "status",
                "status": active_pipeline.status,
                "message": f"Pipeline is {active_pipeline.status}",
            })

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
