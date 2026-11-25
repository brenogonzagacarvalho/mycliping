import os
import re
import time
import uuid
import shutil
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List

# Compatibilidade do faster-whisper com versões de PyAV (av >= 18.0.0)
try:
    import av
    _orig_av_open = av.open

    def _compat_av_open(*args, **kwargs):
        try:
            return _orig_av_open(*args, **kwargs)
        except TypeError as e:
            if "metadata_errors" in str(e):
                kwargs.pop("metadata_errors", None)
                return _orig_av_open(*args, **kwargs)
            raise

    av.open = _compat_av_open
except Exception:
    pass

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import (
    BASE_DIR, UPLOADS_DIR, CACHE_DIR, CPU_NAME, CPU_THREADS, RAM_GB,
    load_settings, save_settings, recommended_concurrent_jobs
)
from app.core.downloader import VideoDownloader
from app.core.live_monitor import live_manager
from app.core.storage import clip_storage
from app.core.clip_pipeline import pipeline, PipelineTask
from app.core.video_renderer import detect_hw_encoder, HW_ENCODERS
from app.core.social_publisher import social_publisher

logger = logging.getLogger("mycliping.api")


def _cleanup_stale_files(max_age_hours: float = 6.0):
    """Removes leftovers from crashed/aborted jobs (temp audio, half-finished downloads)."""
    cutoff = time.time() - max_age_hours * 3600
    try:
        for wav in CACHE_DIR.glob("*_audio.wav"):
            if wav.stat().st_mtime < cutoff:
                wav.unlink(missing_ok=True)
        for folder in UPLOADS_DIR.iterdir():
            if not folder.is_dir() or folder.stat().st_mtime > cutoff:
                continue
            files = list(folder.iterdir())
            only_temp = all(
                f.name.endswith((".part", ".ytdl", ".temp")) or "-Frag" in f.name or f.stat().st_size == 0
                for f in files
            )
            if only_temp:
                shutil.rmtree(folder, ignore_errors=True)
    except Exception as e:
        logger.warning(f"Startup cleanup failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _cleanup_stale_files)
    loop.run_in_executor(None, detect_hw_encoder)  # warm up encoder detection
    social_publisher.start_scheduler()
    yield
    # Don't leave orphan FFmpeg recorders or schedulers running after the server stops
    social_publisher.stop_scheduler()
    live_manager.stop_all()


app = FastAPI(title="ClipStudio AI - Local Video & Live Clipper", version="1.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)


manager = ConnectionManager()


# Request schemas (None = use the saved settings)
class ProcessURLRequest(BaseModel):
    url: str
    max_clips: Optional[int] = None
    min_clip_duration: Optional[int] = None
    max_clip_duration: Optional[int] = None
    caption_style: Optional[str] = None
    layout_mode: Optional[str] = None
    aspect_ratio: Optional[str] = None
    whisper_model: Optional[str] = None
    whisper_language: Optional[str] = None


class LiveStartRequest(BaseModel):
    url: str


class LiveClipRequest(BaseModel):
    session_id: str
    seconds: Optional[int] = 60
    caption_style: Optional[str] = None
    layout_mode: Optional[str] = None
    aspect_ratio: Optional[str] = None


def _safe_filename(name: Optional[str]) -> str:
    base = Path(name or "video.mp4").name
    base = re.sub(r"[^\w.\- ]", "_", base).strip(" .") or "video.mp4"
    stem, ext = os.path.splitext(base)
    return f"{stem[:100]}{ext[:10] or '.mp4'}"


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def get_settings():
    return load_settings()


@app.post("/api/settings")
def update_settings(data: dict):
    return save_settings(data)


@app.get("/api/system")
async def get_system_info():
    encoder = await asyncio.to_thread(detect_hw_encoder)
    settings = load_settings()
    return {
        "cpu_name": CPU_NAME,
        "cpu_threads": CPU_THREADS,
        "ram_gb": RAM_GB,
        "encoder": encoder,
        "gpu_encoding": encoder in HW_ENCODERS,
        "recommended_concurrent_jobs": recommended_concurrent_jobs(),
        "max_concurrent_jobs": settings.get("max_concurrent_jobs"),
        "render_workers": pipeline._render_workers(settings, encoder, 99),
        **pipeline.queue_stats(),
    }


@app.get("/api/info")
async def get_media_info(url: str):
    try:
        return await asyncio.to_thread(VideoDownloader().get_info, url)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/clips/process")
async def start_process_url(req: ProcessURLRequest, background_tasks: BackgroundTasks):
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    pipeline.tasks[task_id] = PipelineTask(task_id=task_id, title=req.url)

    background_tasks.add_task(
        pipeline.process_video_job,
        task_id=task_id,
        source_type="url",
        source_target=req.url,
        options=req.model_dump(),
        ws_callback=manager.broadcast
    )
    return {"task_id": task_id, "status": "queued"}


@app.post("/api/upload")
async def upload_local_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    max_clips: Optional[int] = Form(None),
    min_clip_duration: Optional[int] = Form(None),
    max_clip_duration: Optional[int] = Form(None),
    caption_style: Optional[str] = Form(None),
    layout_mode: Optional[str] = Form(None),
    aspect_ratio: Optional[str] = Form(None),
    whisper_model: Optional[str] = Form(None),
    whisper_language: Optional[str] = Form(None),
):
    task_id = f"upload_{uuid.uuid4().hex[:8]}"
    upload_folder = UPLOADS_DIR / task_id
    upload_folder.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(file.filename)
    dest_file = upload_folder / safe_name

    def _save():
        with open(dest_file, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer, length=4 * 1024 * 1024)

    await asyncio.to_thread(_save)

    pipeline.tasks[task_id] = PipelineTask(task_id=task_id, title=safe_name)

    options = {
        "title": Path(file.filename or safe_name).stem,
        "max_clips": max_clips,
        "min_clip_duration": min_clip_duration,
        "max_clip_duration": max_clip_duration,
        "caption_style": caption_style,
        "layout_mode": layout_mode,
        "aspect_ratio": aspect_ratio,
        "whisper_model": whisper_model,
        "whisper_language": whisper_language,
    }

    background_tasks.add_task(
        pipeline.process_video_job,
        task_id=task_id,
        source_type="upload",
        source_target=str(dest_file.resolve()),
        options=options,
        ws_callback=manager.broadcast
    )
    return {"task_id": task_id, "status": "queued"}


@app.get("/api/tasks/{task_id}")
def get_task_status(task_id: str):
    task = pipeline.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    return {
        "task_id": task.task_id,
        "title": task.title,
        "status": task.status,
        "stage": task.stage,
        "progress": task.progress,
        "message": task.message,
        "clips": task.clips,
        "error": task.error
    }


@app.get("/api/tasks")
def list_tasks():
    tasks = sorted(pipeline.tasks.values(), key=lambda t: t.created_at, reverse=True)
    return {
        **pipeline.queue_stats(),
        "tasks": [
            {"task_id": t.task_id, "title": t.title, "status": t.status, "stage": t.stage,
             "progress": t.progress, "message": t.message}
            for t in tasks[:30]
        ],
    }


# ---------------------------------------------------------------------------
# Live Stream Routes
# ---------------------------------------------------------------------------

@app.post("/api/live/start")
async def start_live_monitor(req: LiveStartRequest):
    try:
        return await asyncio.to_thread(live_manager.start_monitoring, req.url)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/live/stop")
async def stop_live_monitor(session_id: str):
    success = await asyncio.to_thread(live_manager.stop_monitoring, session_id)
    return {"success": success}


@app.get("/api/live/status/{session_id}")
def get_live_status(session_id: str):
    return live_manager.get_status(session_id)


@app.post("/api/live/clip")
async def clip_from_live(req: LiveClipRequest, background_tasks: BackgroundTasks):
    """Extracts the last N seconds of the live stream and submits it to the pipeline."""
    session = live_manager.sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão ao vivo não encontrada")

    seconds = max(10, min(600, req.seconds or 60))
    try:
        raw_clip_path = await asyncio.to_thread(live_manager.clip_last_seconds, req.session_id, seconds)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    task_id = f"liveclip_{uuid.uuid4().hex[:8]}"
    pipeline.tasks[task_id] = PipelineTask(task_id=task_id, title=f"Live Clip: {session.title}")

    options = {
        "title": f"Live Highlight ({seconds}s)",
        "max_clips": 1,
        "min_clip_duration": min(15, seconds),
        "max_clip_duration": seconds,
        "caption_style": req.caption_style,
        "layout_mode": req.layout_mode,
        "aspect_ratio": req.aspect_ratio,
    }

    background_tasks.add_task(
        pipeline.process_video_job,
        task_id=task_id,
        source_type="live_clip",
        source_target=raw_clip_path,
        options=options,
        ws_callback=manager.broadcast
    )
    return {"task_id": task_id, "status": "queued"}


# ---------------------------------------------------------------------------
# Gallery & File Serving Routes
# ---------------------------------------------------------------------------

@app.get("/api/gallery")
def get_gallery():
    return clip_storage.list_clips()


@app.get("/api/clips/{clip_id}/video")
def serve_clip_video(clip_id: str):
    clip = clip_storage.get_clip(clip_id)
    if not clip or not os.path.exists(clip["video_path"]):
        raise HTTPException(status_code=404, detail="Vídeo do clipe não encontrado")
    return FileResponse(clip["video_path"], media_type="video/mp4")


@app.get("/api/clips/{clip_id}/thumb")
def serve_clip_thumb(clip_id: str):
    clip = clip_storage.get_clip(clip_id)
    if not clip or not os.path.exists(clip["thumb_path"]):
        raise HTTPException(status_code=404, detail="Miniatura não encontrada")
    return FileResponse(clip["thumb_path"], media_type="image/jpeg")


class ClipUpdateRequest(BaseModel):
    hook_title: Optional[str] = None
    summary: Optional[str] = None
    hashtags: Optional[List[str]] = None


class BatchPostRequest(BaseModel):
    clip_ids: List[str]
    platforms: List[str]  # ["youtube_shorts", "tiktok"]
    start_timestamp: Optional[float] = None
    interval_hours: float = 0.0


@app.patch("/api/clips/{clip_id}")
def update_clip_metadata(clip_id: str, req: ClipUpdateRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    updated = clip_storage.update_clip(clip_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Clipe não encontrado")
    return updated


@app.delete("/api/clips/{clip_id}")
def delete_clip(clip_id: str):
    return {"success": clip_storage.delete_clip(clip_id)}


# ---------------------------------------------------------------------------
# Social Auto-Post Routes (YouTube Shorts, TikTok & Webhook)
# ---------------------------------------------------------------------------

@app.get("/api/social/status")
def get_social_status():
    return social_publisher.get_public_status()


@app.post("/api/social/config")
def save_social_config(payload: dict):
    return social_publisher.update_credentials(payload)


@app.post("/api/social/disconnect/{platform}")
def disconnect_social_platform(platform: str):
    social_publisher.disconnect(platform)
    return {"success": True}


@app.get("/api/social/youtube/auth-url")
def youtube_auth_url(request: Request):
    redirect_uri = str(request.base_url).rstrip("/") + "/api/social/youtube/callback"
    try:
        return {"url": social_publisher.youtube_auth_url(redirect_uri), "redirect_uri": redirect_uri}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/social/youtube/callback")
def youtube_oauth_callback(request: Request, code: Optional[str] = None, error: Optional[str] = None):
    if error or not code:
        return HTMLResponse(f"<h3>Erro na autorização do YouTube: {error or 'código ausente'}</h3>")
    redirect_uri = str(request.base_url).rstrip("/") + "/api/social/youtube/callback"
    try:
        social_publisher.youtube_handle_callback(code, redirect_uri)
        return RedirectResponse(url="/?social=youtube_ok#tab-autopost")
    except Exception as e:
        return HTMLResponse(f"<h3>Falha ao conectar YouTube: {e}</h3>")


@app.get("/api/social/tiktok/auth-url")
def tiktok_auth_url(request: Request):
    redirect_uri = str(request.base_url).rstrip("/") + "/api/social/tiktok/callback"
    try:
        return {"url": social_publisher.tiktok_auth_url(redirect_uri), "redirect_uri": redirect_uri}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/social/tiktok/callback")
def tiktok_oauth_callback(request: Request, code: Optional[str] = None, error: Optional[str] = None):
    if error or not code:
        return HTMLResponse(f"<h3>Erro na autorização do TikTok: {error or 'código ausente'}</h3>")
    redirect_uri = str(request.base_url).rstrip("/") + "/api/social/tiktok/callback"
    try:
        social_publisher.tiktok_handle_callback(code, redirect_uri)
        return RedirectResponse(url="/?social=tiktok_ok#tab-autopost")
    except Exception as e:
        return HTMLResponse(f"<h3>Falha ao conectar TikTok: {e}</h3>")


@app.get("/api/social/queue")
def list_social_queue():
    return social_publisher.list_queue()


@app.post("/api/social/queue/batch")
def enqueue_social_batch(req: BatchPostRequest, background_tasks: BackgroundTasks):
    if not req.clip_ids or not req.platforms:
        raise HTTPException(status_code=400, detail="Selecione ao menos 1 clipe e 1 plataforma.")
    items = social_publisher.enqueue_posts(
        clip_ids=req.clip_ids,
        platforms=req.platforms,
        start_timestamp=req.start_timestamp,
        interval_hours=req.interval_hours,
    )
    # Trigger immediate upload for any item due right now
    now = time.time()
    for item in items:
        if item["scheduled_at"] <= now + 5:
            background_tasks.add_task(social_publisher.process_item, item)
    return {"created": len(items), "items": items}


@app.delete("/api/social/queue/{post_id}")
def delete_social_queue_item(post_id: str):
    return {"success": social_publisher.delete_queue_item(post_id)}


@app.post("/api/social/webhook/test")
def test_social_webhook():
    import requests as _req
    wh = social_publisher.get_config()["webhook"]
    if not wh.get("url"):
        raise HTTPException(status_code=400, detail="Informe a URL do Webhook primeiro.")
    headers = {"X-ClipStudio-Secret": wh["secret"]} if wh.get("secret") else {}
    try:
        res = _req.post(
            wh["url"],
            headers=headers,
            data={
                "test": "true",
                "platform": "tiktok",
                "title": "Teste de conexão ClipStudio AI",
                "description": "Teste de webhook funcionando!",
                "hashtags": "#viral #shorts",
            },
            timeout=15,
        )
        res.raise_for_status()
        return {"ok": True, "status_code": res.status_code, "response": res.text[:200]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Falha ao chamar webhook: {e}")


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/tasks")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keepalive / ping
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Static frontend files
# ---------------------------------------------------------------------------

static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def serve_index():
    index_file = BASE_DIR / "static" / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>ClipStudio AI Backend Online</h1>")
