import os
import re
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Callable
import yt_dlp
from app.config import UPLOADS_DIR, CACHE_DIR, FFMPEG_PATH

logger = logging.getLogger("mycliping.downloader")

class VideoDownloader:
    def __init__(self):
        self.ffmpeg_path = FFMPEG_PATH

    def get_info(self, url: str) -> Dict[str, Any]:
        """Extract metadata without downloading."""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "ffmpeg_location": os.path.dirname(self.ffmpeg_path),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                return {
                    "id": info.get("id", str(uuid.uuid4())[:8]),
                    "title": info.get("title", "Sem título"),
                    "duration": info.get("duration", 0),
                    "thumbnail": info.get("thumbnail", ""),
                    "uploader": info.get("uploader", "Desconhecido"),
                    "is_live": info.get("is_live", False) or info.get("live_status") == "is_live",
                    "view_count": info.get("view_count", 0),
                    "url": url,
                    "webpage_url": info.get("webpage_url", url),
                }
            except Exception as e:
                logger.error(f"Failed to extract info for {url}: {e}")
                raise RuntimeError(f"Não foi possível obter dados do vídeo/live: {str(e)}")

    def download_video(
        self,
        url: str,
        output_name: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Dict[str, Any]:
        """Download video using yt-dlp with progress callback."""
        if not output_name:
            output_name = f"video_{uuid.uuid4().hex[:8]}"

        target_dir = UPLOADS_DIR / output_name
        target_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(target_dir / "source.%(ext)s")

        def hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                speed = d.get("speed", 0) or 0
                pct = (downloaded / total * 100) if total > 0 else 0
                speed_mb = speed / (1024 * 1024)
                msg = f"Baixando: {pct:.1f}% ({speed_mb:.1f} MB/s)"
                if progress_callback:
                    progress_callback(pct, msg)
            elif d.get("status") == "finished":
                if progress_callback:
                    progress_callback(100.0, "Download concluído, finalizando processamento...")

        ydl_opts = {
            "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            "progress_hooks": [hook],
            "quiet": True,
            "no_warnings": True,
            "ffmpeg_location": os.path.dirname(self.ffmpeg_path),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                final_filename = target_dir / "source.mp4"
                
                # If merged into source.mp4 or another extension
                if not final_filename.exists():
                    possible = list(target_dir.glob("source.*"))
                    if possible:
                        final_filename = possible[0]

                return {
                    "video_id": output_name,
                    "title": info.get("title", output_name),
                    "duration": info.get("duration", 0),
                    "thumbnail": info.get("thumbnail", ""),
                    "file_path": str(final_filename.resolve()),
                    "target_dir": str(target_dir.resolve())
                }
        except Exception as e:
            logger.error(f"Download failed for {url}: {e}")
            raise RuntimeError(f"Erro ao baixar vídeo: {str(e)}")
