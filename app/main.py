import os
import time
import uuid
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

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

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import (
    BASE_DIR, DATA_DIR, UPLOADS_DIR, CLIPS_DIR,
    load_settings, save_settings
)
from app.core.downloader import VideoDownloader
from app.core.live_monitor import live_manager
from app.core.storage import clip_storage
from app.core.clip_pipeline import pipeline, PipelineTask

logger = logging.getLogger("mycliping.api")

app = FastAPI(title="ClipStudio AI - Local Video & Live Clipper", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active WebSocket connections
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

# Request schemas
class ProcessURLRequest(BaseModel):
    url: str
    max_clips: Optional[int] = 3
    min_clip_duration: Optional[int] = 25
    max_clip_duration: Optional[int] = 60
    caption_style: Optional[str] = "mrbeast"
    layout_mode: Optional[str] = "smart_face"
    aspect_ratio: Optional[str] = "9:16"
    whisper_model: Optional[str] = None
    whisper_language: Optional[str] = None

class LiveStartRequest(BaseModel):
    url: str

class LiveClipRequest(BaseModel):
    session_id: str
    seconds: Optional[int] = 60
    caption_style: Optional[str] = "mrbeast"
    layout_mode: Optional[str] = "smart_face"
    aspect_ratio: Optional[str] = "9:16"

# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def get_settings():
    return load_settings()

@app.post("/api/settings")
def update_settings(data: dict):
    return save_settings(data)

@app.get("/api/info")
def get_media_info(url: str):
    dl = VideoDownloader()
    return dl.get_info(url)

@app.post("/api/clips/process")
async def start_process_url(req: ProcessURLRequest, background_tasks: BackgroundTasks):
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    task = PipelineTask(task_id=task_id, title=req.url)
    pipeline.tasks[task_id] = task

    options = req.model_dump()
    background_tasks.add_task(
        pipeline.process_video_job,
        task_id=task_id,
        source_type="url",
        source_target=req.url,
        options=options,
        ws_callback=manager.broadcast
    )

    return {"task_id": task_id, "status": "queued"}

@app.post("/api/upload")
async def upload_local_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    max_clips: int = Form(3),
    min_clip_duration: int = Form(25),
    max_clip_duration: int = Form(60),
    caption_style: str = Form("mrbeast"),
    layout_mode: str = Form("smart_face"),
    aspect_ratio: str = Form("9:16"),
    whisper_model: Optional[str] = Form(None),
    whisper_language: Optional[str] = Form(None),
):
    task_id = f"upload_{uuid.uuid4().hex[:8]}"
    upload_folder = UPLOADS_DIR / task_id
    upload_folder.mkdir(parents=True, exist_ok=True)

    dest_file = upload_folder / file.filename
    with open(dest_file, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            buffer.write(chunk)

    task = PipelineTask(task_id=task_id, title=file.filename)
    pipeline.tasks[task_id] = task

    options = {
        "title": file.filename,
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

# ---------------------------------------------------------------------------
# Live Stream Routes
# ---------------------------------------------------------------------------

@app.post("/api/live/start")
def start_live_monitor(req: LiveStartRequest):
    return live_manager.start_monitoring(req.url)

@app.post("/api/live/stop")
def stop_live_monitor(session_id: str):
    success = live_manager.stop_monitoring(session_id)
    return {"success": success}

@app.get("/api/live/status/{session_id}")
def get_live_status(session_id: str):
    return live_manager.get_status(session_id)

@app.post("/api/live/clip")
async def clip_from_live(req: LiveClipRequest, background_tasks: BackgroundTasks):
    """
    Extracts the last N seconds of the live stream and submits to the pipeline!
    """
    session = live_manager.sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sessão ao vivo não encontrada")

    raw_clip_path = live_manager.clip_last_seconds(req.session_id, req.seconds or 60)
    
    task_id = f"liveclip_{uuid.uuid4().hex[:8]}"
    task = PipelineTask(task_id=task_id, title=f"Live Clip: {session.title}")
    pipeline.tasks[task_id] = task

    options = {
        "title": f"Live Highlight ({req.seconds}s)",
        "max_clips": 1,
        "min_clip_duration": 15,
        "max_clip_duration": req.seconds or 60,
        "caption_style": req.caption_style or "mrbeast",
        "layout_mode": req.layout_mode or "smart_face",
        "aspect_ratio": req.aspect_ratio or "9:16",
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

@app.delete("/api/clips/{clip_id}")
def delete_clip(clip_id: str):
    success = clip_storage.delete_clip(clip_id)
    return {"success": success}

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/tasks")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keepalive / ping
            await websocket.receive_text()
    except WebSocketDisconnect:
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
