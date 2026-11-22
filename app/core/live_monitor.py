import os
import time
import math
import uuid
import shutil
import logging
import threading
import subprocess
from typing import Optional, Dict, Any
import yt_dlp
from app.config import LIVE_BUFFER_DIR, FFMPEG_PATH, SUBPROCESS_FLAGS

logger = logging.getLogger("mycliping.live_monitor")


class LiveSession:
    def __init__(self, session_id: str, stream_url: str, title: str, channel: str, hls_url: str):
        self.session_id = session_id
        self.stream_url = stream_url
        self.title = title
        self.channel = channel
        self.hls_url = hls_url
        self.buffer_dir = LIVE_BUFFER_DIR / session_id
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.buffer_dir / "ffmpeg.log"
        self.log_handle = None
        self.is_active = False
        self.process: Optional[subprocess.Popen] = None
        self.start_time = time.time()
        self.chunk_duration = 5  # 5-second chunks
        self.max_chunks = 120    # 120 chunks * 5s = 10 minutes rolling buffer


class LiveMonitorManager:
    def __init__(self):
        self.sessions: Dict[str, LiveSession] = {}
        self.ffmpeg_path = FFMPEG_PATH

    def resolve_live_stream(self, url: str) -> Dict[str, Any]:
        """Resolves any live stream (Twitch, YouTube, Kick, M3U8) to a direct playable stream."""
        if ".m3u8" in url or url.startswith(("rtmp://", "rtmps://")):
            return {"title": "Live Stream HLS/RTMP", "channel": "Stream Direto", "hls_url": url,
                    "is_live": True, "thumbnail": "", "stream_url": url}

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            # 1080p max: higher resolutions only waste disk/CPU for 9:16 crops
            "format": "best[height<=1080]/best",
            "ffmpeg_location": os.path.dirname(self.ffmpeg_path),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                logger.error(f"Failed to resolve live stream for {url}: {e}")
                raise RuntimeError(f"Erro ao conectar na live: {str(e)}")

        if not (info.get("is_live") or info.get("live_status") == "is_live"):
            raise RuntimeError("Este link não é uma transmissão ao vivo ativa. Use a aba de vídeos para VODs.")

        hls_url = info.get("url")
        if not hls_url and "formats" in info:
            formats = [f for f in info["formats"] if f.get("url")]
            hls_formats = [f for f in formats if "m3u8" in (f.get("protocol") or "") or ".m3u8" in f.get("url", "")]
            pick = hls_formats or formats
            if pick:
                hls_url = pick[-1]["url"]
        if not hls_url:
            raise RuntimeError("Não foi possível encontrar a transmissão ao vivo da URL.")

        return {
            "title": info.get("title", "Transmissão Ao Vivo"),
            "channel": info.get("uploader", info.get("channel", "Streamer")),
            "hls_url": hls_url,
            "is_live": True,
            "thumbnail": info.get("thumbnail", ""),
            "stream_url": url
        }

    def start_monitoring(self, url: str) -> Dict[str, Any]:
        """Starts background rolling DVR buffer for the live stream."""
        info = self.resolve_live_stream(url)
        session_id = f"live_{uuid.uuid4().hex[:8]}"
        session = LiveSession(session_id, url, info["title"], info["channel"], info["hls_url"])

        chunk_pattern = str(session.buffer_dir / "chunk_%05d.mp4")
        list_file = str(session.buffer_dir / "chunks.txt")

        # Stream copy into 5s segments (practically 0% CPU)
        cmd = [
            self.ffmpeg_path, "-y", "-hide_banner", "-nostdin", "-loglevel", "warning",
            "-reconnect", "1",
            "-reconnect_at_eof", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", session.hls_url,
            "-map", "0:v:0?", "-map", "0:a:0?",
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(session.chunk_duration),
            "-segment_format", "mp4",
            "-segment_list", list_file,
            "-segment_list_flags", "+live",
            "-reset_timestamps", "1",
            chunk_pattern
        ]

        logger.info(f"Starting live monitoring process for session {session_id}...")
        try:
            # stderr goes to a log FILE: an unread PIPE fills up and freezes FFmpeg
            session.log_handle = open(session.log_path, "wb")
            session.process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=session.log_handle,
                creationflags=SUBPROCESS_FLAGS
            )
        except Exception as e:
            if session.log_handle:
                session.log_handle.close()
            logger.error(f"Failed to start FFmpeg live recording: {e}")
            raise RuntimeError(f"Erro ao iniciar gravação em tempo real: {str(e)}")

        session.is_active = True
        self.sessions[session_id] = session

        def cleaner():
            while session.is_active and session.process and session.process.poll() is None:
                time.sleep(10)
                try:
                    chunks = sorted(session.buffer_dir.glob("chunk_*.mp4"), key=lambda p: p.stat().st_mtime)
                    for old_chunk in chunks[:-session.max_chunks]:
                        try:
                            old_chunk.unlink(missing_ok=True)
                        except Exception:
                            pass
                except Exception as err:
                    logger.warning(f"Error pruning live buffer: {err}")

        threading.Thread(target=cleaner, daemon=True).start()

        return {
            "session_id": session_id,
            "title": session.title,
            "channel": session.channel,
            "hls_url": session.hls_url,
            "status": "recording_buffer"
        }

    def stop_monitoring(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if not session:
            return False

        session.is_active = False
        if session.process and session.process.poll() is None:
            try:
                session.process.terminate()
                session.process.wait(timeout=5)
            except Exception:
                try:
                    session.process.kill()
                except Exception:
                    pass
        if session.log_handle:
            try:
                session.log_handle.close()
            except Exception:
                pass
        # Free disk: raw buffer chunks are useless once the session ends
        shutil.rmtree(session.buffer_dir, ignore_errors=True)
        self.sessions.pop(session_id, None)
        return True

    def stop_all(self):
        for sid in list(self.sessions.keys()):
            self.stop_monitoring(sid)

    def _log_tail(self, session: LiveSession, n: int = 400) -> str:
        try:
            data = session.log_path.read_bytes()[-n:]
            return data.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""

    def get_status(self, session_id: str) -> Dict[str, Any]:
        session = self.sessions.get(session_id)
        if not session:
            return {"active": False, "error": "Sessão não encontrada"}

        chunks = list(session.buffer_dir.glob("chunk_*.mp4"))
        running = bool(session.process and session.process.poll() is None)
        status = {
            "session_id": session_id,
            "title": session.title,
            "channel": session.channel,
            "active": session.is_active and running,
            "buffered_seconds": max(0, len(chunks) - 1) * session.chunk_duration,
            "total_chunks": len(chunks),
            "elapsed_seconds": int(time.time() - session.start_time)
        }
        if session.is_active and not running:
            status["error"] = "A gravação da live parou. " + self._log_tail(session)
        return status

    def clip_last_seconds(self, session_id: str, seconds: int = 60) -> str:
        """Instantly extracts the last N seconds of the live stream by concatenating rolling chunks."""
        session = self.sessions.get(session_id)
        if not session:
            raise RuntimeError("Sessão ao vivo não encontrada.")

        chunks = sorted(session.buffer_dir.glob("chunk_*.mp4"), key=lambda p: p.stat().st_mtime)
        # Ignore the very last chunk: it's still being written
        available_chunks = chunks[:-1]
        if not available_chunks:
            raise RuntimeError("Aguarde alguns segundos para que a live preencha o buffer de gravação.")

        num_chunks_needed = max(1, math.ceil(seconds / session.chunk_duration))
        selected_chunks = available_chunks[-num_chunks_needed:]

        clip_id = f"liveclip_{uuid.uuid4().hex[:8]}"
        out_clip_dir = LIVE_BUFFER_DIR / "exported"
        out_clip_dir.mkdir(parents=True, exist_ok=True)
        out_clip_path = out_clip_dir / f"{clip_id}.mp4"

        concat_txt = session.buffer_dir / f"concat_{clip_id}.txt"
        with open(concat_txt, "w", encoding="utf-8") as f:
            for chk in selected_chunks:
                chk_path = str(chk.resolve()).replace("\\", "/")
                f.write(f"file '{chk_path}'\n")

        cmd = [
            self.ffmpeg_path, "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_txt.resolve()),
            "-c", "copy",
            "-movflags", "+faststart",
            str(out_clip_path.resolve())
        ]

        logger.info(f"Extracting live clip ({seconds}s) with {len(selected_chunks)} chunks...")
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", creationflags=SUBPROCESS_FLAGS)
        concat_txt.unlink(missing_ok=True)

        if res.returncode != 0 or not out_clip_path.exists():
            logger.error(f"Concat failed: {res.stderr}")
            raise RuntimeError("Erro ao concatenar pedaços da live.")

        logger.info(f"Successfully generated raw live clip: {out_clip_path}")
        return str(out_clip_path.resolve())


# Singleton instance
live_manager = LiveMonitorManager()
