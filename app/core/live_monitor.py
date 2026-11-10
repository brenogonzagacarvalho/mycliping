import os
import sys
import time
import uuid
import shutil
import logging
import threading
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List
import yt_dlp
from app.config import LIVE_BUFFER_DIR, FFMPEG_PATH

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
        # Direct HLS or RTMP link
        if url.endswith(".m3u8") or url.startswith("rtmp://") or url.startswith("rtmps://"):
            return {
                "title": "Live Stream HLS/RTMP",
                "channel": "Stream Direto",
                "hls_url": url,
                "is_live": True,
                "thumbnail": "",
                "stream_url": url
            }

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "ffmpeg_location": os.path.dirname(self.ffmpeg_path),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                # Find direct m3u8 or best stream URL
                hls_url = info.get("url")
                if not hls_url and "formats" in info:
                    # Pick best format that has both video and audio or hls
                    formats = [f for f in info["formats"] if f.get("url")]
                    hls_formats = [f for f in formats if "m3u8" in f.get("protocol", "") or ".m3u8" in f.get("url", "")]
                    if hls_formats:
                        hls_url = hls_formats[-1]["url"]
                    elif formats:
                        hls_url = formats[-1]["url"]

                if not hls_url:
                    raise RuntimeError("Não foi possível encontrar a transmissão ao vivo da URL.")

                return {
                    "title": info.get("title", "Transmissão Ao Vivo"),
                    "channel": info.get("uploader", info.get("channel", "Streamer")),
                    "hls_url": hls_url,
                    "is_live": info.get("is_live", True),
                    "thumbnail": info.get("thumbnail", ""),
                    "stream_url": url
                }
            except Exception as e:
                logger.error(f"Failed to resolve live stream for {url}: {e}")
                raise RuntimeError(f"Erro ao conectar na live: {str(e)}")

    def start_monitoring(self, url: str) -> Dict[str, Any]:
        """Starts background rolling DVR buffer for the live stream."""
        info = self.resolve_live_stream(url)
        session_id = f"live_{uuid.uuid4().hex[:8]}"

        session = LiveSession(
            session_id=session_id,
            stream_url=url,
            title=info["title"],
            channel=info["channel"],
            hls_url=info["hls_url"]
        )

        # Launch FFmpeg to record rolling segment chunks
        # Uses segment format with segment_time 5s and copy codec (virtually 0% CPU)
        chunk_pattern = str(session.buffer_dir / "chunk_%05d.mp4")
        list_file = str(session.buffer_dir / "chunks.txt")

        # FFmpeg command: record incoming live stream into rolling 5-second segments
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-reconnect", "1",
            "-reconnect_at_eof", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", session.hls_url,
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
            # Creation flags for hidden window on Windows
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            session.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creationflags
            )
            session.is_active = True
            self.sessions[session_id] = session

            # Start cleaner thread to keep only the last max_chunks
            def cleaner():
                while session.is_active and session.process and session.process.poll() is None:
                    time.sleep(10)
                    try:
                        chunks = sorted(
                            list(session.buffer_dir.glob("chunk_*.mp4")),
                            key=lambda p: p.stat().st_mtime
                        )
                        if len(chunks) > session.max_chunks:
                            to_delete = chunks[:-session.max_chunks]
                            for old_chunk in to_delete:
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
        except Exception as e:
            logger.error(f"Failed to start FFmpeg live recording: {e}")
            raise RuntimeError(f"Erro ao iniciar gravação em tempo real: {str(e)}")

    def stop_monitoring(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if not session:
            return False

        session.is_active = False
        if session.process:
            try:
                session.process.terminate()
                session.process.wait(timeout=3)
            except Exception:
                try:
                    session.process.kill()
                except Exception:
                    pass

        # Cleanup after a delay or keep for inspection
        return True

    def get_status(self, session_id: str) -> Dict[str, Any]:
        session = self.sessions.get(session_id)
        if not session:
            return {"active": False, "error": "Sessão não encontrada"}

        chunks = sorted(
            list(session.buffer_dir.glob("chunk_*.mp4")),
            key=lambda p: p.stat().st_mtime
        )
        buffered_seconds = len(chunks) * session.chunk_duration
        elapsed = time.time() - session.start_time

        return {
            "session_id": session_id,
            "title": session.title,
            "channel": session.channel,
            "active": session.is_active and (session.process and session.process.poll() is None),
            "buffered_seconds": buffered_seconds,
            "total_chunks": len(chunks),
            "elapsed_seconds": int(elapsed)
        }

    def clip_last_seconds(self, session_id: str, seconds: int = 60) -> str:
        """Instantly extracts the last N seconds of the live stream by concatenating rolling chunks."""
        session = self.sessions.get(session_id)
        if not session:
            raise RuntimeError("Sessão ao vivo não encontrada.")

        chunks = sorted(
            list(session.buffer_dir.glob("chunk_*.mp4")),
            key=lambda p: p.stat().st_mtime
        )

        if not chunks:
            raise RuntimeError("Aguarde alguns segundos para que a live preencha o buffer de gravação.")

        # Ignore the very last chunk if it's currently being written
        available_chunks = chunks[:-1] if len(chunks) > 1 else chunks

        num_chunks_needed = max(1, int(seconds / session.chunk_duration))
        selected_chunks = available_chunks[-num_chunks_needed:]

        clip_id = f"liveclip_{uuid.uuid4().hex[:8]}"
        out_clip_dir = LIVE_BUFFER_DIR / "exported"
        out_clip_dir.mkdir(parents=True, exist_ok=True)
        out_clip_path = out_clip_dir / f"{clip_id}.mp4"

        # Concat list file
        concat_txt = session.buffer_dir / f"concat_{clip_id}.txt"
        with open(concat_txt, "w", encoding="utf-8") as f:
            for chk in selected_chunks:
                # Escape backslashes for FFmpeg concat demuxer
                chk_path = str(chk.resolve()).replace("\\", "/")
                f.write(f"file '{chk_path}'\n")

        # Fast concat with stream copy (instantaneous, no re-encoding)
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_txt.resolve()),
            "-c", "copy",
            str(out_clip_path.resolve())
        ]

        logger.info(f"Extracting live clip ({seconds}s) with {len(selected_chunks)} chunks...")
        res = subprocess.run(cmd, capture_output=True, text=True)
        try:
            concat_txt.unlink(missing_ok=True)
        except Exception:
            pass

        if res.returncode != 0 or not out_clip_path.exists():
            logger.error(f"Concat failed: {res.stderr}")
            raise RuntimeError("Erro ao concatenar pedaços da live.")

        logger.info(f"Successfully generated raw live clip: {out_clip_path}")
        return str(out_clip_path.resolve())

# Singleton instance
live_manager = LiveMonitorManager()
