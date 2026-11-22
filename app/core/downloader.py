import os
import re
import time
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Callable
import yt_dlp
from app.config import UPLOADS_DIR, FFMPEG_PATH

logger = logging.getLogger("mycliping.downloader")

# Prefer H.264 (avc1): AV1/VP9 are much slower to decode on weak CPUs and
# OpenCV frequently can't open AV1 at all.
DOWNLOAD_FORMAT = (
    "bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/"
    "bv*[vcodec^=avc1][height<=1080]+ba/"
    "b[vcodec^=avc1][height<=1080]/"
    "bv*[height<=1080]+ba/"
    "b[height<=1080]/b"
)

_TEMP_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp")


def _is_temp_file(p: Path) -> bool:
    name = p.name.lower()
    return name.endswith(_TEMP_SUFFIXES) or "-frag" in name or ".part-" in name


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
        """Download video using yt-dlp with (throttled) progress callback."""
        if not output_name:
            output_name = f"video_{uuid.uuid4().hex[:8]}"

        target_dir = UPLOADS_DIR / output_name
        target_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(target_dir / "source.%(ext)s")
        last_emit = {"t": 0.0}

        def hook(d):
            if not progress_callback:
                return
            status = d.get("status")
            if status == "downloading":
                now = time.time()
                if now - last_emit["t"] < 0.7:
                    return
                last_emit["t"] = now
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0) or 0
                if total > 0:
                    pct = downloaded / total * 100
                elif d.get("fragment_count"):
                    pct = (d.get("fragment_index") or 0) / d["fragment_count"] * 100
                else:
                    pct = 0.0
                speed_mb = (d.get("speed") or 0) / (1024 * 1024)
                eta = d.get("eta")
                eta_txt = f" • ETA {int(eta) // 60:02d}:{int(eta) % 60:02d}" if eta else ""
                progress_callback(min(99.0, pct), f"Baixando: {pct:.1f}% ({speed_mb:.1f} MB/s){eta_txt}")
            elif status == "finished":
                progress_callback(100.0, "Download concluído, finalizando arquivo...")

        ydl_opts = {
            "format": DOWNLOAD_FORMAT,
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            "progress_hooks": [hook],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "concurrent_fragment_downloads": 4,  # big speed-up for Twitch/Kick/HLS VODs
            "retries": 10,
            "fragment_retries": 10,
            "ffmpeg_location": os.path.dirname(self.ffmpeg_path),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as e:
            logger.error(f"Download failed for {url}: {e}")
            raise RuntimeError(f"Erro ao baixar vídeo: {str(e)}")

        final_filename: Optional[Path] = None
        # yt-dlp reports the real final path(s)
        for req in info.get("requested_downloads") or []:
            fp = req.get("filepath") or req.get("_filename")
            if fp and Path(fp).exists() and not _is_temp_file(Path(fp)):
                final_filename = Path(fp)
                break

        if final_filename is None:
            candidate = target_dir / "source.mp4"
            if candidate.exists():
                final_filename = candidate
            else:
                possible = [p for p in target_dir.glob("source.*") if not _is_temp_file(p)]
                if possible:
                    final_filename = max(possible, key=lambda p: p.stat().st_size)

        if final_filename is None or final_filename.stat().st_size == 0:
            raise RuntimeError("Download terminou, mas o arquivo de vídeo final não foi encontrado.")

        # Remove leftover fragments
        for p in target_dir.iterdir():
            if p != final_filename and _is_temp_file(p):
                try:
                    p.unlink()
                except Exception:
                    pass

        return {
            "video_id": output_name,
            "title": info.get("title", output_name),
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail", ""),
            "file_path": str(final_filename.resolve()),
            "target_dir": str(target_dir.resolve())
        }
