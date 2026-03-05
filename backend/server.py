import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.match_db import MatchDB
from backend.pipeline import Pipeline
from backend.project_config import ProjectConfig
from backend.task_manager import TaskManager
from backend.utils import load_config, logger, get_openai_key, get_gemini_key
from backend.footballia_navigator import FootballiaNavigator
from backend.batch_manager import BatchManager
from backend.stats_aggregator import StatsAggregator
from backend.exporter import DatasetExporter
from backend.annotation_exporter import AnnotationExporter
from backend.resample_runner import ResampleRunner

app = FastAPI(title="Footballia Screenshotter")

app.mount("/static", StaticFiles(directory="frontend"), name="static")

# Serve captured frames and exports as static files
recordings_dir = Path("recordings")
recordings_dir.mkdir(exist_ok=True)
app.mount("/recordings", StaticFiles(directory="recordings"), name="recordings")

exports_dir = Path("exports")
exports_dir.mkdir(exist_ok=True)
app.mount("/exports", StaticFiles(directory="exports"), name="exports")

# Global state
active_pipeline: Pipeline | None = None
pipeline_task: asyncio.Task | None = None
ws_clients: set[WebSocket] = set()
state_store: dict = {}  # For pending generic web source
navigator_instance: Optional[FootballiaNavigator] = None
batch_manager: Optional[BatchManager] = None
active_resample: Optional[ResampleRunner] = None
resample_task_obj: Optional[asyncio.Task] = None
resample_tasks: dict[str, dict] = {}


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
    custom_ranges: list[dict] = []         # [{"start": "15:00", "end": "20:00"}, ...]
    local_filepath: str = ""               # For local_file source
    generic_web_url: str = ""              # For generic_web source
    sequence_profiles: dict | None = None  # Sequence capture profile overrides


async def broadcast(message: dict):
    dead = set()
    for ws in ws_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


# ── Startup check ──

def check_incomplete_captures():
    """Check for captures that were running when the server last stopped."""
    db = MatchDB()
    incomplete = db.conn.execute(
        "SELECT id, match_id FROM captures WHERE status = 'running' OR status = 'in_progress'"
    ).fetchall()
    if incomplete:
        # Mark them as interrupted
        for cap_id, match_id in incomplete:
            db.conn.execute(
                "UPDATE captures SET status = 'interrupted' WHERE id = ?",
                (cap_id,),
            )
        db.conn.commit()
        logger.warning(f"Found {len(incomplete)} interrupted capture(s) from previous session")
    db.close()
    return len(incomplete)


@app.on_event("startup")
async def startup_event():
    check_incomplete_captures()


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


@app.delete("/api/project")
async def delete_project():
    """Delete the current project, all matches, captures, and recordings."""
    global active_pipeline, pipeline_task

    # Refuse if a capture is running
    if active_pipeline and active_pipeline.status == "capturing":
        return {"status": "error", "message": "Cannot delete project while a capture is running. Stop the capture first."}

    deleted = {"project_config": False, "database": False, "recordings": 0, "batch_state": False}

    try:
        # 1. Delete project config
        pc = ProjectConfig()
        if pc.exists:
            pc.delete()
            deleted["project_config"] = True

        # 2. Delete SQLite database and WAL files
        db_path = Path("data/matches.db")
        for suffix in ["", "-shm", "-wal"]:
            p = db_path.parent / (db_path.name + suffix)
            if p.exists():
                p.unlink()
                deleted["database"] = True

        # 3. Delete all recordings
        rec_dir = Path("recordings")
        if rec_dir.exists():
            for child in rec_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                    deleted["recordings"] += 1
                elif child.is_file():
                    child.unlink()

        # 4. Delete batch state if present
        batch_state = Path("data/batch_state.json")
        if batch_state.exists():
            batch_state.unlink()
            deleted["batch_state"] = True

        # 5. Delete exports directory contents
        exports_dir = Path("exports")
        if exports_dir.exists():
            for child in exports_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                elif child.is_file():
                    child.unlink()

        logger.info(f"Project deleted: {deleted}")
        return {"status": "ok", "deleted": deleted}

    except Exception as e:
        import traceback
        logger.error(f"Project delete failed: {e}\n{traceback.format_exc()}")
        return {"status": "error", "message": f"Delete failed: {e}"}


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
    match = db.get_match(match_id)
    if not match:
        db.close()
        return {"status": "error", "message": "Match not found"}
    db.delete_match(match_id)
    db.close()
    return {"status": "ok", "deleted_match_id": match_id}


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
        "sequence_profiles": config.get("sequence_profiles", {}),
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


# ── Provider test route ──

@app.post("/api/providers/test")
async def test_provider(body: dict):
    """
    Test an AI provider's API key by sending a tiny test image.
    Returns success/error status so the UI can validate before capture starts.
    """
    provider = body.get("provider", "")
    if not provider or provider == "manual":
        return {"status": "ok", "message": "Manual mode requires no API key."}

    try:
        # Create a minimal 8x8 gray JPEG test image
        from PIL import Image
        import io
        img = Image.new("RGB", (8, 8), color=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        test_jpeg = buf.getvalue()

        # Build a minimal task config
        task_config = {
            "id": "_api_test",
            "name": "API Test",
            "prompt": "Reply with exactly: {\"test\": \"ok\"}",
            "classification_field": "test",
            "categories": ["ok"],
        }

        config = load_config()
        classifier_config = dict(config)

        if provider == "openai":
            api_key = get_openai_key()
            if not api_key or api_key == "sk-your-key-here":
                return {"status": "error", "message": "OpenAI API key not set. Add OPENAI_API_KEY to your .env file."}
            classifier_config["api_key"] = api_key
            classifier_config["model"] = config.get("openai", {}).get("model", "gpt-4o-mini")
        elif provider == "gemini":
            api_key = get_gemini_key()
            if not api_key or api_key == "your-gemini-key-here":
                return {"status": "error", "message": "Gemini API key not set. Add GEMINI_API_KEY to your .env file."}
            classifier_config["gemini_api_key"] = api_key
            classifier_config["gemini_model"] = config.get("gemini", {}).get("model", "gemini-2.0-flash")
        else:
            return {"status": "error", "message": f"Unknown provider: {provider}"}

        from backend.classifiers import create_classifier
        classifier = create_classifier(provider, task_config, classifier_config)
        result = await classifier.classify_frame(test_jpeg)

        if result.get("api_error"):
            return {
                "status": "error",
                "message": f"API key invalid or service unavailable: {result.get('reasoning', 'Unknown error')}",
            }

        return {"status": "ok", "message": f"{provider.title()} API is working."}

    except Exception as e:
        logger.error(f"Provider test failed: {e}")
        return {"status": "error", "message": str(e)}


# ── Capture routes ──

@app.post("/api/capture/start")
async def start_capture(body: CaptureRequest):
    global active_pipeline, pipeline_task

    if active_pipeline and active_pipeline.status == "capturing":
        return {"status": "error", "message": "Capture already in progress"}

    try:
        config = load_config()

        match_data = dict(body.match_data) if body.match_data else {}
        source_type = body.source_type or "footballia"

        # ── Create the appropriate source ──
        if source_type == "footballia":
            match_data["footballia_url"] = body.footballia_url
            from backend.sources.footballia import FootballiaSource
            source = FootballiaSource(config.get("browser", {}))
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
            custom_ranges=body.custom_ranges,
            sequence_profiles=body.sequence_profiles,
        )

        pipeline_task = asyncio.create_task(active_pipeline.run())
        logger.info(f"Capture started: provider={body.provider}, task={body.task_id}, mode={body.capture_mode}, source={source_type}")

        return {"status": "started", "capture_id": capture_id}

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Capture start failed: {e}\n{tb}")
        return {"status": "error", "message": f"Capture failed: {e}"}


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
        sequence_profiles=body.sequence_profiles,
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


# ── Match preview (scrape without capture) ──

@app.post("/api/match/preview")
async def preview_match(body: dict):
    """Scrape a Footballia match page for metadata (teams, lineups, goals) without starting capture."""
    url = body.get("url", "")
    if not url:
        return {"error": "URL required"}

    try:
        from backend.sources.footballia import FootballiaSource
        source = FootballiaSource(load_config().get("browser", {}))
        launched = await source.setup(url=url, broadcast_fn=broadcast, navigate_only=True)
        if not launched:
            return {"error": "Failed to open Footballia page"}

        data = source.match_data or {}
        # Don't close — browser can be reused for capture
        return {
            "status": "ok",
            "home_team": data.get("home_team", ""),
            "away_team": data.get("away_team", ""),
            "competition": data.get("competition", ""),
            "season": data.get("season", ""),
            "stage": data.get("stage", ""),
            "venue": data.get("venue", ""),
            "date": data.get("date", ""),
            "home_lineup": data.get("home_lineup", []),
            "away_lineup": data.get("away_lineup", []),
            "home_coach": data.get("home_coach"),
            "away_coach": data.get("away_coach"),
            "goals": data.get("goals", []),
            "result": data.get("result"),
            "scrape_success": data.get("scrape_success", False),
        }
    except Exception as e:
        logger.error(f"Match preview failed: {e}")
        return {"error": f"Preview failed: {e}"}


# ── Navigator routes ──

@app.post("/api/navigator/scrape")
async def scrape_person_page(body: dict):
    """Scrape a Footballia coach/player page for match discovery."""
    url = body.get("url", "")
    if not url:
        return {"error": "URL required"}

    global navigator_instance
    if navigator_instance is None:
        navigator_instance = FootballiaNavigator()

    # We need a Playwright page. Reuse the Footballia source's browser if available,
    # or launch a temporary one.
    from backend.sources.footballia import FootballiaSource
    source = FootballiaSource({})
    launched = await source.setup(url=url, broadcast_fn=broadcast, navigate_only=True)
    if not launched:
        return {"error": "Failed to open browser"}

    data = await navigator_instance.scrape_person_page(source._page, url, broadcast_fn=broadcast)
    # Don't close the browser — user might want to browse more
    return data


@app.post("/api/navigator/scrape-team")
async def scrape_team_page(body: dict):
    """Scrape a Footballia team page for match discovery."""
    url = body.get("url", "")
    if not url:
        return {"error": "URL required"}

    global navigator_instance
    if navigator_instance is None:
        navigator_instance = FootballiaNavigator()

    from backend.sources.footballia import FootballiaSource
    source = FootballiaSource({})
    launched = await source.setup(url=url, broadcast_fn=broadcast, navigate_only=True)
    if not launched:
        return {"error": "Failed to open browser"}

    data = await navigator_instance.scrape_team_page(source._page, url, broadcast_fn=broadcast)
    return data


@app.post("/api/navigator/filter")
async def filter_navigator_matches(body: dict):
    """Filter previously scraped navigator data."""
    nav = FootballiaNavigator()
    data = body.get("data", {})
    matches = nav.filter_matches(
        data,
        club=body.get("club"),
        season=body.get("season"),
        competition=body.get("competition"),
        role=body.get("role"),
    )
    return {"matches": matches, "count": len(matches)}


@app.post("/api/navigator/add-to-library")
async def add_navigator_matches(body: dict):
    """Add selected matches from navigator to the Match Library."""
    matches = body.get("matches", [])
    db = MatchDB()
    added = 0
    match_ids = []
    for m in matches:
        # Determine opponent based on home/away
        team_name = m.get("team_name", "")
        opponent = ""
        if m.get("home_away") == "H":
            opponent = m.get("away_team", "")
        elif m.get("home_away") == "A":
            opponent = m.get("home_team", "")
        else:
            opponent = m.get("away_team") or m.get("home_team", "")

        try:
            mid = db.add_match(
                match_day=0,
                date=m.get("date", ""),
                home_away=m.get("home_away", ""),
                opponent=opponent,
                score=m.get("score", ""),
                footballia_url=m.get("full_url", ""),
                team_name=team_name,
                season=m.get("season", ""),
                competition=m.get("competition", ""),
            )
            added += 1
            match_ids.append(mid)
        except Exception as e:
            logger.warning(f"Failed to add match: {e}")
    db.close()
    return {"added": added, "match_ids": match_ids}


# ── Batch capture routes ──

@app.post("/api/batch/create")
async def create_batch(body: dict):
    """Create a new batch capture queue."""
    global batch_manager
    batch_manager = BatchManager(broadcast_fn=broadcast)

    batch_id = batch_manager.create_batch(
        matches=body.get("matches", []),
        targets=body.get("targets", {}),
        provider=body.get("provider", "openai"),
        task_id=body.get("task_id", "camera_angle"),
        capture_mode=body.get("capture_mode", "full_match"),
        delay_between=body.get("delay_between", 30),
    )
    return {"batch_id": batch_id, "count": len(body.get("matches", []))}


@app.post("/api/batch/start")
async def start_batch():
    """Start executing the batch queue."""
    if not batch_manager:
        return {"error": "No batch created"}

    async def pipeline_factory(**kwargs):
        """Create and run a Pipeline for one match in the batch."""
        from backend.sources.footballia import FootballiaSource
        config = load_config()
        source = FootballiaSource(config.get("browser", {}))
        success = await source.setup(url=kwargs["match_url"], broadcast_fn=broadcast)
        if not success:
            raise RuntimeError(f"Failed to load {kwargs['match_label']}")

        db = MatchDB()
        capture_id = db.create_capture(
            match_id=kwargs.get("match_id"),
            provider=kwargs["provider"],
            source_type="footballia",
            config={"targets": kwargs["targets"]},
        )

        pipeline = Pipeline(
            source=source,
            match={"opponent": kwargs["match_label"], "id": kwargs.get("match_id")},
            targets=kwargs["targets"],
            start_time="00:00",
            config=config,
            broadcast_fn=broadcast,
            capture_id=capture_id,
            db=db,
            provider=kwargs["provider"],
            task_id=kwargs["task_id"],
            capture_mode=kwargs["capture_mode"],
        )

        await pipeline.run()

        return {
            "frames_captured": sum(pipeline.saved_counts.values()),
            "api_cost": pipeline.classifier.get_cost(),
        }

    # Run batch in background task
    asyncio.create_task(batch_manager.run(pipeline_factory))
    return {"status": "started"}


@app.post("/api/batch/pause")
async def pause_batch():
    if batch_manager:
        batch_manager.pause()
    return {"status": "paused"}


@app.post("/api/batch/resume")
async def resume_batch():
    if batch_manager:
        batch_manager.resume()
    return {"status": "resumed"}


@app.post("/api/batch/cancel")
async def cancel_batch():
    if batch_manager:
        batch_manager.cancel()
    return {"status": "cancelled"}


@app.get("/api/batch/state")
async def get_batch_state():
    if batch_manager:
        return batch_manager.get_state() or {"status": "none"}
    return {"status": "none"}


# ── Statistics route ──

@app.get("/api/stats")
async def get_season_stats():
    """Get aggregate season statistics."""
    agg = StatsAggregator()
    stats = agg.get_season_stats()
    feedback = agg.get_correction_feedback()
    if feedback:
        stats["annotation_feedback"] = feedback
    agg.close()
    return stats


# ── Export routes ──

@app.post("/api/export")
async def export_dataset(body: dict):
    """Export dataset in a standard format."""
    fmt = body.get("format", "csv")  # coco, imagenet, csv, huggingface
    output_path = body.get("output_path", f"exports/{fmt}_{int(time.time())}")
    capture_ids = body.get("capture_ids")
    match_ids = body.get("match_ids")

    exp = DatasetExporter()
    try:
        if fmt == "coco":
            path = exp.export_coco(output_path, capture_ids, match_ids)
        elif fmt == "imagenet":
            path = exp.export_imagenet(output_path, capture_ids, match_ids)
        elif fmt == "csv":
            if not output_path.endswith(".csv"):
                output_path += "/frames.csv"
            path = exp.export_csv(output_path, capture_ids, match_ids)
        elif fmt == "huggingface":
            path = exp.export_huggingface(output_path, capture_ids, match_ids)
        else:
            return {"error": f"Unknown format: {fmt}"}
        return {"status": "ok", "path": path, "format": fmt}
    except Exception as e:
        return {"error": str(e)}
    finally:
        exp.close()


# ── Annotation export route ──

@app.post("/api/export-annotation")
async def export_annotation(body: dict):
    """Export captured frames as a bundle for the annotation tool."""
    match_id = body.get("match_id")
    if not match_id:
        return {"status": "error", "message": "match_id is required"}

    exporter = AnnotationExporter(match_id, broadcast_fn=broadcast)
    try:
        result = await exporter.export_async()
        return result
    except Exception as e:
        logger.error(f"Annotation export failed: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        exporter.close()


# ── Collections routes ──

@app.get("/api/collections")
async def list_collections():
    db = MatchDB()
    result = db.get_collections()
    db.close()
    return result


@app.post("/api/collections")
async def create_collection(body: dict):
    db = MatchDB()
    cid = db.create_collection(body.get("name", ""), body.get("description", ""))
    if body.get("match_ids"):
        db.add_to_collection(cid, body["match_ids"])
    db.close()
    return {"id": cid}


@app.post("/api/collections/{collection_id}/add")
async def add_to_collection(collection_id: int, body: dict):
    db = MatchDB()
    db.add_to_collection(collection_id, body.get("match_ids", []))
    db.close()
    return {"status": "ok"}


@app.get("/api/collections/{collection_id}/matches")
async def get_collection_matches(collection_id: int):
    db = MatchDB()
    matches = db.get_collection_matches(collection_id)
    db.close()
    return matches


# ── Gallery batch accept route ──

@app.post("/api/gallery/batch-accept")
async def batch_accept_frames(body: dict):
    """Accept all unreviewed frames above a confidence threshold."""
    capture_id = body.get("capture_id")
    threshold = body.get("threshold", 0.85)

    if not capture_id:
        return {"error": "capture_id required"}

    db = MatchDB()
    accepted = db.batch_accept_frames(capture_id, threshold)
    db.close()
    return {"accepted": accepted}


# ── Category samples route ──

@app.get("/api/category-samples")
async def get_category_samples():
    """
    Return one high-confidence sample image path per category.
    Used for the reference image popup.
    """
    db = MatchDB()
    samples = {}
    categories = db.conn.execute(
        "SELECT DISTINCT camera_type FROM frames WHERE confidence > 0.85"
    ).fetchall()

    for (cat,) in categories:
        row = db.conn.execute(
            "SELECT filepath FROM frames WHERE camera_type = ? AND confidence > 0.85 "
            "ORDER BY confidence DESC LIMIT 1",
            (cat,),
        ).fetchone()
        if row and Path(row[0]).exists():
            # Return relative path from recordings/
            fp = Path(row[0])
            try:
                rel = fp.relative_to(Path("recordings"))
                samples[cat] = str(rel)
            except ValueError:
                pass

    db.close()
    return samples


# ── Custom task save route ──

@app.post("/api/tasks/test-prompt")
async def test_custom_prompt(body: dict):
    """
    Test a custom prompt by running it against a sample frame.
    Uses the most recent captured frame, or returns a mock result if none available.
    """
    prompt = body.get("prompt", "")
    classification_field = body.get("classification_field", "result")
    provider = body.get("provider", "openai")

    if not prompt:
        return {"error": "No prompt provided"}

    # Find a recent frame to test with
    db = MatchDB()
    row = db.conn.execute(
        "SELECT filepath FROM frames ORDER BY id DESC LIMIT 1"
    ).fetchone()
    db.close()

    if row and Path(row[0]).exists():
        frame_bytes = Path(row[0]).read_bytes()
    else:
        return {
            "error": "No captured frames found to test with. "
                     "Capture at least one frame first, then test your prompt.",
        }

    # Build a temporary task config for the classifier
    task_config = {
        "id": "_test",
        "name": "Test",
        "prompt": prompt,
        "classification_field": classification_field,
        "categories": body.get("categories", []),
    }

    try:
        from backend.classifiers import create_classifier
        config = load_config()
        classifier_config = dict(config)

        # Ensure classifier gets the keys in the format it expects
        if provider == "openai":
            classifier_config["api_key"] = get_openai_key()
            classifier_config["model"] = config.get("openai", {}).get("model", "gpt-4o-mini")
        elif provider == "gemini":
            classifier_config["gemini_api_key"] = get_gemini_key()
            classifier_config["gemini_model"] = config.get("gemini", {}).get("model", "gemini-2.0-flash")

        classifier = create_classifier(
            provider=provider,
            task=task_config,
            config=classifier_config,
        )
        result = await classifier.classify_frame(frame_bytes)
        return {
            "status": "ok",
            "classified_as": result.get("classified_as"),
            "confidence": result.get("confidence"),
            "raw_response": result.get("raw_response"),
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/tasks/custom")
async def save_custom_task(body: dict):
    """Save a user-created custom task template."""
    tm = TaskManager()
    errors = tm.validate_task(body)
    if errors:
        return {"error": "Validation failed", "details": errors}
    path = tm.save_custom_task(body)
    return {"status": "ok", "path": path}


# ── Sequence capture routes ──

@app.get("/api/sequence/profiles")
async def get_sequence_profiles():
    """Return current sequence profile configurations."""
    config = load_config()
    seq = config.get("sequence_profiles", {})

    # If pipeline is running, return its live config
    if active_pipeline and active_pipeline.sequence_dispatcher:
        seq = active_pipeline.sequence_dispatcher.get_profiles_config()

    return seq


@app.put("/api/sequence/profiles")
async def update_sequence_profiles(body: dict):
    """Update sequence profile configurations from frontend."""
    # Update the live dispatcher if pipeline is running
    if active_pipeline and active_pipeline.sequence_dispatcher:
        active_pipeline.sequence_dispatcher.update_profiles(body)

    return {"status": "ok"}


@app.get("/api/sequence/status")
async def get_sequence_status():
    """Return current FSM states for all profiles."""
    if active_pipeline and active_pipeline.sequence_dispatcher:
        return active_pipeline.sequence_dispatcher.get_all_status()
    return {}


@app.get("/api/sequence/summary")
async def get_sequence_summary():
    """Return session totals for sequence captures."""
    if active_pipeline and active_pipeline.sequence_dispatcher:
        return active_pipeline.sequence_dispatcher.get_session_summary()
    return {}


# ── File browse ──

class BrowseFileRequest(BaseModel):
    title: str = "Select a file"
    filetypes: list[list[str]] = []  # e.g. [["JSON files", "*.json"]]
    initial_dir: str = ""


@app.post("/api/browse-file")
async def browse_file(body: BrowseFileRequest):
    """Open a native file picker dialog and return the selected path."""
    import subprocess, platform, sys

    system = platform.system()

    if system == "Darwin":
        # macOS: use osascript to open native Finder dialog
        # Build file type filter from extensions (e.g. "*.json" -> "json")
        # On newer macOS (Ventura+), `choose file of type` requires UTI
        # strings — plain extensions alone may not match.  Include both
        # the extension and the UTI so the dialog works on all versions.
        _EXT_TO_UTI = {
            "json": "public.json",
            "txt": "public.plain-text",
            "csv": "public.comma-separated-values-text",
            "xlsx": "org.openxmlformats.spreadsheetml.sheet",
            "xls": "com.microsoft.excel.xls",
            "mp4": "public.mpeg-4",
            "mov": "com.apple.quicktime-movie",
            "avi": "public.avi",
            "mkv": "public.mkv",
            "webm": "org.webmproject.webm",
            "ts": "public.mpeg-2-transport-stream",
            "png": "public.png",
            "jpg": "public.jpeg",
            "jpeg": "public.jpeg",
        }
        type_list = []
        for ft in body.filetypes:
            if len(ft) >= 2:
                # ft[1] is like "*.json" or "*.mp4 *.mkv *.avi"
                for ext in ft[1].split():
                    ext_clean = ext.lstrip("*.")
                    if ext_clean:
                        type_list.append(f'"{ext_clean}"')
                        uti = _EXT_TO_UTI.get(ext_clean.lower())
                        if uti:
                            type_list.append(f'"{uti}"')

        # 'activate' brings the dialog to front above the browser window
        script_parts = ['activate\n']
        script_parts.append('set chosenFile to POSIX path of (choose file')
        script_parts.append(f' with prompt "{body.title}"')
        if type_list:
            script_parts.append(f' of type {{{", ".join(type_list)}}}')
        if body.initial_dir:
            script_parts.append(f' default location POSIX file "{body.initial_dir}"')
        script_parts.append(')')
        script = "".join(script_parts)

        try:
            result = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await result.communicate()
            if result.returncode == 0 and stdout:
                selected = stdout.decode().strip()
                return {"status": "ok", "path": selected}
            else:
                # User cancelled or error
                return {"status": "cancelled", "path": ""}
        except Exception as e:
            logger.warning(f"File browse failed: {e}")
            return {"status": "error", "message": str(e)}

    elif system == "Linux":
        # Linux: use zenity
        cmd = ["zenity", "--file-selection", f"--title={body.title}"]
        try:
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await result.communicate()
            if result.returncode == 0 and stdout:
                return {"status": "ok", "path": stdout.decode().strip()}
            return {"status": "cancelled", "path": ""}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    else:
        return {"status": "error", "message": f"File browsing not supported on {system}"}


@app.post("/api/upload-temp")
async def upload_temp(file: UploadFile):
    """Save uploaded file to a temp location and return the server-side path.
    Used as fallback when native file picker (osascript) is unavailable."""
    import tempfile
    suffix = Path(file.filename).suffix if file.filename else ""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="screenshotter_")
    content = await file.read()
    tmp.write(content)
    tmp.close()
    return {"status": "ok", "path": tmp.name, "filename": file.filename}


# ── Resample routes ──

class ResampleImportRequest(BaseModel):
    file_path: str


@app.post("/api/resample/import")
async def import_resample(body: ResampleImportRequest):
    """Import a resample_request.json file and create a resample task."""
    import uuid as _uuid

    file_path = Path(body.file_path)
    if not file_path.exists():
        return {"status": "error", "message": f"File not found: {body.file_path}"}

    try:
        with open(file_path) as f:
            data = json.load(f)
    except Exception as e:
        return {"status": "error", "message": f"Invalid JSON: {e}"}

    # Extract match info from the resample request.
    # Support both nested format (match_info.match_url) and flat format.
    match_info = data.get("match_info", {})
    match_url = match_info.get("match_url", "") or data.get("match_url", "")
    match_id_str = match_info.get("match_id", "") or data.get("match_id", "")

    # Support both "resample_targets" (annotation-tool export) and "targets"
    raw_targets = data.get("resample_targets", []) or data.get("targets", [])

    # Flatten nested format (annotation-tool export) into the flat format
    # expected by ResampleRunner and the frontend.
    # Nested format: each entry has "target_player" + "sequences" list
    # Flat format: each entry has player_name, video_time_start, etc.
    targets: list[dict] = []
    if raw_targets and "target_player" in raw_targets[0]:
        for rt in raw_targets:
            player = rt.get("target_player", {})
            player_name = player.get("name", "Unknown")
            jersey = player.get("jersey_number", "")
            for seq in rt.get("sequences", []):
                targets.append({
                    "player_name": player_name,
                    "jersey_number": jersey,
                    "camera_type": seq.get("camera_angle", "UNKNOWN"),
                    "video_time_start": seq.get("video_time_start", 0),
                    "video_time_end": seq.get("video_time_end", 0),
                    "original_sequence_id": seq.get("sequence_id", ""),
                    "original_interval": seq.get("original_interval_sec", 0.5),
                    "reason": seq.get("sequence_type", "annotation_gap"),
                    "expected_new_frames": seq.get("expected_new_frames", 0),
                    "enabled": True,
                })
    else:
        # Already flat format — use as-is
        targets = raw_targets

    if not targets:
        return {"status": "error", "message": "No targets found in resample request"}

    # Try to match against library by URL, then by match_id folder name
    db = MatchDB()
    all_matches = db.get_all_matches()
    db.close()

    matched_match = None
    if match_url:
        for m in all_matches:
            if m.get("footballia_url") == match_url:
                matched_match = m
                break

    if not matched_match and match_id_str:
        for m in all_matches:
            md = int(m.get("md", 0))
            opponent = (m.get("opponent", "Unknown")).replace(" ", "_")
            date = m.get("date", "unknown")
            folder = f"MD{md:02d}_{opponent}_{date}"
            if folder == match_id_str:
                matched_match = m
                break

    # Build task
    task_id = str(_uuid.uuid4())[:8]
    task = {
        "id": task_id,
        "status": "pending",
        "file_path": str(file_path),
        "match_url": match_url,
        "match_id": match_id_str,
        "matched_match": matched_match,
        "targets": targets,
        "settings": {
            "interval": (data.get("generation_settings", {}).get("estimated_resample_interval")
                         or data.get("settings", {}).get("interval", 0.3)),
            "seek_buffer": data.get("settings", {}).get("seek_buffer", 2.0),
        },
        "created_at": time.time(),
    }
    resample_tasks[task_id] = task

    return {
        "status": "ok",
        "task_id": task_id,
        "match_found": matched_match is not None,
        "match_label": matched_match.get("opponent", "") if matched_match else None,
        "target_count": len(targets),
    }


@app.get("/api/resample/tasks")
async def get_resample_tasks():
    """Return all resample tasks."""
    return list(resample_tasks.values())


@app.get("/api/resample/tasks/{task_id}")
async def get_resample_task(task_id: str):
    """Return detail for a single resample task."""
    task = resample_tasks.get(task_id)
    if not task:
        return {"status": "error", "message": "Task not found"}
    # Merge live status if this task is actively running
    if active_resample and active_resample.task_id == task_id:
        task["live_status"] = active_resample.get_status()
    return task


@app.put("/api/resample/tasks/{task_id}")
async def update_resample_task(task_id: str, body: dict):
    """Update settings or toggle targets for a resample task."""
    task = resample_tasks.get(task_id)
    if not task:
        return {"status": "error", "message": "Task not found"}

    if "settings" in body:
        task["settings"].update(body["settings"])
    if "targets" in body:
        task["targets"] = body["targets"]
    return {"status": "ok"}


@app.delete("/api/resample/tasks/{task_id}")
async def delete_resample_task(task_id: str):
    """Delete a resample task entirely."""
    if task_id not in resample_tasks:
        return {"status": "error", "message": "Task not found"}
    # Don't allow deleting a running task
    if active_resample and active_resample.task_id == task_id and active_resample.status == "capturing":
        return {"status": "error", "message": "Cannot delete a running task. Stop it first."}
    del resample_tasks[task_id]
    return {"status": "ok"}


@app.delete("/api/resample/tasks/{task_id}/targets/{target_index}")
async def delete_resample_target(task_id: str, target_index: int):
    """Delete a single target from a resample task by index."""
    task = resample_tasks.get(task_id)
    if not task:
        return {"status": "error", "message": "Task not found"}
    if target_index < 0 or target_index >= len(task["targets"]):
        return {"status": "error", "message": "Target index out of range"}
    task["targets"].pop(target_index)
    return {"status": "ok", "remaining": len(task["targets"])}


@app.post("/api/resample/tasks/{task_id}/start")
async def start_resample(task_id: str):
    """Start executing a resample task."""
    global active_resample, resample_task_obj

    if active_pipeline and active_pipeline.status == "capturing":
        return {"status": "error", "message": "A capture is already in progress"}
    if active_resample and active_resample.status == "capturing":
        return {"status": "error", "message": "A resample is already in progress"}

    task = resample_tasks.get(task_id)
    if not task:
        return {"status": "error", "message": "Task not found"}

    match = task.get("matched_match") or {}
    targets = [t for t in task["targets"] if t.get("enabled", True)]

    if not targets:
        return {"status": "error", "message": "No enabled targets"}

    # Determine match URL
    match_url = task.get("match_url", "") or match.get("footballia_url", "")
    if not match_url:
        return {"status": "error", "message": "No match URL available. Add a Footballia URL to the matched library entry."}

    try:
        config = load_config()

        # Create source — use Footballia source
        from backend.sources.footballia import FootballiaSource
        source = FootballiaSource(config.get("browser", {}))
        success = await source.setup(url=match_url, broadcast_fn=broadcast)
        if not success:
            return {"status": "error", "message": "Failed to connect to video"}

        # Create output manager
        from backend.output_manager import OutputManager
        output = OutputManager(match=match, base_dir="recordings")

        active_resample = ResampleRunner(
            source=source,
            match=match,
            targets=targets,
            settings=task["settings"],
            broadcast_fn=broadcast,
            output_manager=output,
            task_id=task_id,
            resample_source_match=task.get("match_id", ""),
            resample_request_file=task.get("file_path", ""),
        )

        task["status"] = "running"
        resample_task_obj = asyncio.create_task(active_resample.run())
        logger.info(f"Resample started: task={task_id}, targets={len(targets)}")

        return {"status": "started", "target_count": len(targets)}

    except Exception as e:
        import traceback
        logger.error(f"Resample start failed: {e}\n{traceback.format_exc()}")
        return {"status": "error", "message": str(e)}


@app.post("/api/resample/tasks/{task_id}/pause")
async def pause_resample(task_id: str):
    """Pause/resume a running resample."""
    if not active_resample or active_resample.task_id != task_id:
        return {"status": "error", "message": "No active resample for this task"}

    if active_resample.status == "paused":
        active_resample.resume()
        return {"status": "resumed"}
    else:
        active_resample.pause()
        return {"status": "paused"}


@app.post("/api/resample/tasks/{task_id}/stop")
async def stop_resample(task_id: str):
    """Stop a running resample."""
    if not active_resample or active_resample.task_id != task_id:
        return {"status": "error", "message": "No active resample for this task"}
    active_resample.stop()
    task = resample_tasks.get(task_id)
    if task:
        task["status"] = "completed"
    return {"status": "stopped"}


@app.post("/api/resample/tasks/{task_id}/skip")
async def skip_resample_target(task_id: str):
    """Skip the current target in a running resample."""
    if not active_resample or active_resample.task_id != task_id:
        return {"status": "error", "message": "No active resample for this task"}
    active_resample.skip_target()
    return {"status": "skipped"}


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
