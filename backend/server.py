import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.match_db import MatchDB
from backend.pipeline import Pipeline
from backend.project_config import ProjectConfig
from backend.task_manager import TaskManager
from backend.utils import load_config, logger, get_openai_key, get_gemini_key

app = FastAPI(title="Footballia Screenshotter")

app.mount("/static", StaticFiles(directory="frontend"), name="static")

# Global state
active_pipeline: Pipeline | None = None
pipeline_task: asyncio.Task | None = None
ws_clients: set[WebSocket] = set()
state_store: dict = {}  # For pending generic web source


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
    footballia_url: str = ""
    targets: dict[str, int]
    start_time: str = "00:00"
    match_data: dict = {}
    source_type: str = "footballia"
    provider: str = "openai"
    task_id: str = "camera_angle"
    capture_mode: str = "full_match"
    goal_times: list[dict] = []
    goal_window: int = 30
    local_filepath: str = ""               # For local_file source
    generic_web_url: str = ""              # For generic_web source


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
    tm = TaskManager()
    default_task = tm.get_task("camera_angle")
    from backend.utils import get_active_categories, get_active_category_descriptions
    categories = get_active_categories(default_task)
    descriptions = get_active_category_descriptions(default_task)

    return {
        "defaults": config["defaults"],
        "sampling_interval": config["sampling"]["interval_seconds"],
        "camera_types": categories,
        "camera_descriptions": descriptions,
    }


# ── Task routes ──

@app.get("/api/tasks")
async def get_tasks():
    """List all available tasks (summary without full prompts)."""
    tm = TaskManager()
    return tm.get_all_tasks()


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """Get full task template by ID."""
    tm = TaskManager()
    try:
        task = tm.get_task(task_id)
        return task
    except FileNotFoundError:
        return {"status": "error", "message": f"Task '{task_id}' not found"}


@app.get("/api/tasks/{task_id}/presets/{preset_id}")
async def get_preset(task_id: str, preset_id: str):
    """Get a specific preset's targets."""
    tm = TaskManager()
    try:
        targets = tm.get_preset_targets(task_id, preset_id)
        return {"task_id": task_id, "preset_id": preset_id, "targets": targets}
    except (FileNotFoundError, KeyError) as e:
        return {"status": "error", "message": str(e)}


# ── Provider routes ──

@app.get("/api/providers")
async def get_providers():
    """List available classification providers and their status."""
    openai_key = get_openai_key()
    gemini_key = get_gemini_key()

    providers = [
        {
            "id": "openai",
            "name": "OpenAI GPT-4o-mini",
            "description": "Best accuracy. ~$0.07 per 1000 frames.",
            "available": bool(openai_key and openai_key != "sk-your-key-here"),
            "cost_per_frame": 0.00007,
        },
        {
            "id": "gemini",
            "name": "Google Gemini Flash",
            "description": "Free tier: 1,500 req/day. Paid: ~$0.04 per 1000 frames.",
            "available": bool(gemini_key and gemini_key != "your-gemini-key-here"),
            "cost_per_frame": 0.00004,
        },
        {
            "id": "manual",
            "name": "Manual Classification",
            "description": "No API needed. Frames saved to PENDING/ for human review.",
            "available": True,
            "cost_per_frame": 0,
        },
    ]
    return providers


# ── Capture routes ──

@app.post("/api/capture/start")
async def start_capture(body: CaptureRequest):
    global active_pipeline, pipeline_task

    if active_pipeline and active_pipeline.status == "capturing":
        return {"status": "error", "message": "Capture already in progress"}

    config = load_config()

    match_data = dict(body.match_data) if body.match_data else {}
    source_type = body.source_type or "footballia"

    # ── Create the appropriate source ──
    if source_type == "footballia":
        match_data["footballia_url"] = body.footballia_url
        from backend.sources.footballia import FootballiaSource
        source = FootballiaSource(config["browser"])
        success = await source.setup(
            url=body.footballia_url,
            broadcast_fn=broadcast,
        )
        if not success:
            return {"status": "error", "message": "Failed to connect to video. Check the URL and try again."}

    elif source_type == "local_file":
        try:
            import cv2
        except ImportError:
            return {"status": "error", "message": "opencv-python not installed. Run: pip install opencv-python"}
        from backend.sources.local_file import LocalFileSource
        source = LocalFileSource(config)
        success = await source.setup(filepath=body.local_filepath, broadcast_fn=broadcast)
        if not success:
            return {"status": "error", "message": "Failed to open video file"}

    elif source_type == "generic_web":
        from backend.sources.generic_web import GenericWebSource
        source = GenericWebSource(config.get("browser", {}))
        success = await source.setup(url=body.generic_web_url, broadcast_fn=broadcast)
        if not success:
            return {"status": "error", "message": "Failed to launch browser"}
        # For generic web, we return here and wait for user to confirm video is playing
        # The source is stored but pipeline is NOT started yet
        state_store["pending_source"] = source
        state_store["pending_body"] = body
        state_store["pending_config"] = config
        return {"status": "waiting_for_video", "message": "Browser is open. Start the video, then click 'Video is playing'."}

    else:
        return {"status": "error", "message": f"Unknown source type: {source_type}"}

    # ── Store scraped data (Footballia only) ──
    db = MatchDB()
    if source_type == "footballia" and hasattr(source, 'match_data') and source.match_data.get("scrape_success"):
        if body.match_id:
            try:
                db.update_match_scraped_data(body.match_id, source.match_data)
            except Exception as e:
                logger.warning(f"Failed to store scraped data: {e}")

    # ── Create capture record ──
    capture_id = None
    if body.match_id:
        try:
            capture_id = db.create_capture(
                match_id=body.match_id,
                provider=body.provider,
                source_type=source_type,
                config={"targets": body.targets, "start_time": body.start_time, "capture_mode": body.capture_mode},
            )
        except Exception as e:
            logger.warning(f"Failed to create capture record: {e}")

    # ── Create and start pipeline ──
    active_pipeline = Pipeline(
        source=source,
        match=match_data,
        targets=body.targets,
        start_time=body.start_time,
        config=config,
        broadcast_fn=broadcast,
        capture_id=capture_id,
        db=db,
        provider=body.provider,
        task_id=body.task_id,
        capture_mode=body.capture_mode,
        goal_times=body.goal_times,
        goal_window=body.goal_window,
    )

    pipeline_task = asyncio.create_task(active_pipeline.run())
    logger.info(f"Capture started: provider={body.provider}, task={body.task_id}, mode={body.capture_mode}, source={source_type}")

    return {"status": "started", "capture_id": capture_id}


@app.post("/api/capture/confirm-video")
async def confirm_video_playing():
    """User confirms video is playing in the generic web browser."""
    global active_pipeline, pipeline_task

    source = state_store.get("pending_source")
    body = state_store.get("pending_body")
    config = state_store.get("pending_config")

    if not source:
        return {"status": "error", "message": "No pending source"}

    # Find the video element
    found = await source.find_video_element(broadcast_fn=broadcast)
    if not found:
        return {"status": "error", "message": "No video found on the page"}

    # Now create and start the pipeline
    db = MatchDB()
    match_data = dict(body.match_data) if body.match_data else {}

    capture_id = None
    if body.match_id:
        capture_id = db.create_capture(
            match_id=body.match_id,
            provider=body.provider,
            source_type="generic_web",
            config={"targets": body.targets},
        )

    active_pipeline = Pipeline(
        source=source,
        match=match_data,
        targets=body.targets,
        start_time=body.start_time,
        config=config,
        broadcast_fn=broadcast,
        capture_id=capture_id,
        db=db,
        provider=body.provider,
        task_id=body.task_id,
        capture_mode=body.capture_mode,
    )

    pipeline_task = asyncio.create_task(active_pipeline.run())

    # Clear pending state
    state_store.clear()
    return {"status": "started", "capture_id": capture_id}


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


# ── Review/Gallery routes ──

@app.get("/api/captures/{capture_id}/frames")
async def get_capture_frames(
    capture_id: int,
    max_confidence: float = 1.0,
    only_anomalies: bool = False,
    only_unreviewed: bool = False,
    only_pending: bool = False,
):
    """Get frames for the Gallery/Review UI with optional filters."""
    db = MatchDB()
    if only_pending:
        frames = db.get_pending_frames(capture_id)
    else:
        frames = db.get_frames_for_review(
            capture_id, max_confidence, only_anomalies, only_unreviewed
        )
    stats = db.get_review_stats(capture_id)
    db.close()
    return {"frames": frames, "stats": stats}


@app.post("/api/frames/{frame_id}/review")
async def review_frame(frame_id: int, body: dict):
    """Classify or reclassify a single frame."""
    new_type = body.get("classified_as", "")
    if not new_type:
        return {"error": "Missing classified_as"}

    db = MatchDB()
    frame = db.conn.execute("SELECT * FROM frames WHERE id = ?", (frame_id,)).fetchone()
    if not frame:
        db.close()
        return {"error": "Frame not found"}

    frame = dict(frame)
    old_type = frame["camera_type"]

    # Move the file if classification changed
    if old_type != new_type and frame["filepath"]:
        from backend.output_manager import OutputManager
        capture = db.conn.execute(
            "SELECT output_dir FROM captures WHERE id = ?", (frame["capture_id"],)
        ).fetchone()
        if capture and capture["output_dir"]:
            new_path = OutputManager.static_move_frame(frame["filepath"], new_type, capture["output_dir"])
            db.conn.execute(
                "UPDATE frames SET filepath = ? WHERE id = ?", (new_path, frame_id)
            )

    db.review_frame(frame_id, new_type)
    db.close()
    return {"status": "ok", "old_type": old_type, "new_type": new_type}


@app.post("/api/captures/{capture_id}/batch-accept")
async def batch_accept(capture_id: int, body: dict):
    """Accept all frames above a confidence threshold."""
    min_confidence = body.get("min_confidence", 0.9)
    db = MatchDB()
    count = db.batch_accept_frames(capture_id, min_confidence)
    stats = db.get_review_stats(capture_id)
    db.close()
    return {"status": "ok", "accepted": count, "stats": stats}


@app.get("/api/captures/{capture_id}/stats")
async def get_capture_review_stats(capture_id: int):
    """Get review statistics for a capture."""
    db = MatchDB()
    stats = db.get_review_stats(capture_id)
    db.close()
    return stats


@app.get("/api/frames/{frame_id}/image")
async def get_frame_image(frame_id: int):
    """Serve a frame image file for the Gallery UI."""
    db = MatchDB()
    frame = db.conn.execute("SELECT filepath FROM frames WHERE id = ?", (frame_id,)).fetchone()
    db.close()
    if not frame or not frame["filepath"]:
        return {"error": "Frame not found"}
    filepath = frame["filepath"]
    if not Path(filepath).exists():
        return {"error": "File not found on disk"}
    return FileResponse(filepath, media_type="image/jpeg")


@app.get("/api/matches/{match_id}/scraped")
async def get_scraped_data(match_id: int):
    """Get scraped Footballia data for a match."""
    db = MatchDB()
    match = db.get_match(match_id)
    db.close()
    if not match:
        return {"error": "Match not found"}
    result = {}
    for field in ["home_lineup_json", "away_lineup_json", "home_coach_json",
                  "away_coach_json", "goals_json", "result_json"]:
        val = match.get(field, "")
        if val:
            try:
                result[field.replace("_json", "")] = json.loads(val)
            except Exception:
                pass
    result["venue"] = match.get("venue", "")
    result["stage"] = match.get("stage", "")
    return result


# ── Platform info ──

@app.get("/api/platform")
async def platform_info():
    """Return platform info and dependency status."""
    from backend.platform_utils import get_platform_info, check_dependencies
    return {
        "platform": get_platform_info(),
        "dependencies": check_dependencies(),
    }


# ── Health check ──

@app.get("/api/health")
async def health_check():
    openai_key = get_openai_key()
    gemini_key = get_gemini_key()

    result = {
        "openai_key_set": bool(openai_key and openai_key != "sk-your-key-here"),
        "gemini_key_set": bool(gemini_key and gemini_key != "your-gemini-key-here"),
    }

    if result["openai_key_set"]:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            models = client.models.list()
            result["openai_model_available"] = any("gpt-4o-mini" in m.id for m in models)
            result["status"] = "ok"
        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)
    else:
        result["status"] = "ok"

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
