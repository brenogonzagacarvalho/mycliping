import os
import re
import subprocess
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any, Callable, Tuple
from app.config import FFMPEG_PATH, SUBPROCESS_FLAGS
from app.core.face_tracker import FaceTracker

logger = logging.getLogger("mycliping.renderer")

HW_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")
_encoder_lock = threading.Lock()
_detected_encoder: Optional[str] = None


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def _test_encoder(name: str) -> bool:
    pix = "nv12" if name == "h264_qsv" else "yuv420p"
    cmd = [
        FFMPEG_PATH, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=0.5",
        "-pix_fmt", pix, "-c:v", name, "-f", "null", "-"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=SUBPROCESS_FLAGS)
        return res.returncode == 0
    except Exception:
        return False


def detect_hw_encoder() -> str:
    """Finds the fastest working H.264 encoder (GPU first, CPU libx264 fallback). Cached."""
    global _detected_encoder
    with _encoder_lock:
        if _detected_encoder is None:
            _detected_encoder = "libx264"
            for enc in HW_ENCODERS:
                if _test_encoder(enc):
                    _detected_encoder = enc
                    break
            logger.info(f"Video encoder selected: {_detected_encoder}")
        return _detected_encoder


def resolve_encoder(preference: Optional[str]) -> str:
    pref = (preference or "auto").strip().lower()
    if pref in ("", "auto"):
        return detect_hw_encoder()
    return pref


def encoder_args(name: str) -> list:
    if name == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "veryfast", "-b:v", "7M", "-maxrate", "9M",
                "-bufsize", "14M", "-pix_fmt", "nv12"]
    if name == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "22", "-b:v", "0",
                "-pix_fmt", "yuv420p"]
    if name == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "21", "-qp_p", "23",
                "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]


class VideoRenderer:
    def __init__(self):
        self.ffmpeg_path = FFMPEG_PATH
        self.face_tracker = FaceTracker()

    # ------------------------------------------------------------------
    # Probing (FFmpeg-based: works for AV1/VP9/HEVC where OpenCV fails)
    # ------------------------------------------------------------------
    def probe_video(self, video_path: str) -> Dict[str, Any]:
        res = subprocess.run(
            [self.ffmpeg_path, "-hide_banner", "-nostdin", "-i", video_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=SUBPROCESS_FLAGS
        )
        info = res.stderr or ""
        m = re.search(r"Video:[^\n]*?\b(\d{2,5})x(\d{2,5})\b", info)
        if not m:
            raise RuntimeError(f"Não foi possível ler o vídeo (sem trilha de vídeo válida): {video_path}")
        w, h = int(m.group(1)), int(m.group(2))
        if re.search(r"rotation of -?(90|270)", info):
            w, h = h, w

        duration = 0.0
        d = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", info)
        if d:
            duration = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
        fps_m = re.search(r"([\d.]+)\s*fps", info)

        return {
            "width": w,
            "height": h,
            "fps": float(fps_m.group(1)) if fps_m else 30.0,
            "duration": duration,
            "has_audio": bool(re.search(r"Stream #\d+:\d+[^\n]*Audio:", info)),
        }

    @staticmethod
    def _fit_crop(src_w: int, src_h: int, ratio: float) -> Tuple[int, int]:
        """Largest crop with aspect `ratio` (w/h) that fits inside the source."""
        if src_w / src_h > ratio:
            return _even(src_h * ratio), _even(src_h)
        return _even(src_w), _even(src_w / ratio)

    # ------------------------------------------------------------------
    # Filter graph
    # ------------------------------------------------------------------
    def _build_video_graph(self, source_video, start_time, end_time, meta, layout_mode, aspect_ratio) -> str:
        src_w, src_h = meta["width"], meta["height"]

        if aspect_ratio == "1:1":
            cw, ch = self._fit_crop(src_w, src_h, 1.0)
            return f"[0:v]crop={cw}:{ch}:{(src_w - cw) // 2}:{(src_h - ch) // 2},scale=1080:1080"

        if aspect_ratio == "16:9":
            return ("[0:v]scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2")

        tw, th = 1080, 1920

        if layout_mode == "smart_face":
            cw, ch = self._fit_crop(src_w, src_h, 9 / 16)
            crop_x = (src_w - cw) // 2
            if cw < src_w:  # only worth tracking if there's horizontal room to move
                detections = self.face_tracker.analyze_face_positions(source_video, start_time, end_time, src_w, src_h)
                center = self.face_tracker.compute_smoothed_crop_x(detections, src_w, cw)
                crop_x = int(max(0, min(src_w - cw, center - cw / 2)))
            crop_y = (src_h - ch) // 2
            return f"[0:v]crop={cw}:{ch}:{crop_x}:{crop_y},scale={tw}:{th}:flags=bicubic"

        if layout_mode == "split_screen":
            top_h, bot_h = 720, 1200  # 37.5% cam + 62.5% gameplay = 1920px, zero black bars
            detections = self.face_tracker.analyze_face_positions(source_video, start_time, end_time, src_w, src_h)
            summary = self.face_tracker.face_summary(detections)
            top_ratio = tw / top_h
            if summary:
                fx, fy, fwn = summary
                cw = min(src_w, max(fwn * src_w * 3.8, src_w * 0.28))
                ch = cw / top_ratio
                if ch > src_h:
                    ch = src_h
                    cw = ch * top_ratio
                cw, ch = _even(cw), _even(ch)
                cx = int(max(0, min(src_w - cw, fx * src_w - cw / 2)))
                cy = int(max(0, min(src_h - ch, fy * src_h - ch / 2)))
            else:
                cw, ch = self._fit_crop(src_w, src_h, top_ratio)
                cx, cy = (src_w - cw) // 2, (src_h - ch) // 2

            bw, bh = self._fit_crop(src_w, src_h, tw / bot_h)
            bx, by = (src_w - bw) // 2, (src_h - bh) // 2
            return (
                f"[0:v]split=2[cam][game];"
                f"[cam]crop={cw}:{ch}:{cx}:{cy},scale={tw}:{top_h}:flags=bicubic,setsar=1[top];"
                f"[game]crop={bw}:{bh}:{bx}:{by},scale={tw}:{bot_h}:flags=bicubic,setsar=1[bot];"
                f"[top][bot]vstack=inputs=2"
            )

        if layout_mode in ("blurred_bg", "blur_bg"):
            # Blur on a tiny copy then upscale: same look, a fraction of the CPU cost
            return (
                "[0:v]split=2[bg][fg];"
                "[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
                f"boxblur=10:2,scale={tw}:{th}[blurred];"
                f"[fg]scale={tw}:{th}:force_original_aspect_ratio=decrease:force_divisible_by=2[sharp];"
                "[blurred][sharp]overlay=(W-w)/2:(H-h)/2"
            )

        # "original" / default: centered 9:16 crop
        cw, ch = self._fit_crop(src_w, src_h, 9 / 16)
        return f"[0:v]crop={cw}:{ch}:{(src_w - cw) // 2}:{(src_h - ch) // 2},scale={tw}:{th}:flags=bicubic"

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------
    def render_clip(
        self,
        source_video: str,
        start_time: float,
        end_time: float,
        output_path: str,
        ass_path: Optional[str] = None,
        layout_mode: str = "smart_face",
        aspect_ratio: str = "9:16",
        progress_callback: Optional[Callable[[float, str], None]] = None,
        encoder: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Trims, reframes, burns subtitles, and renders the final MP4."""
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        meta = meta or self.probe_video(source_video)
        duration = max(0.5, end_time - start_time)

        if progress_callback:
            progress_callback(10.0, "Analisando enquadramento e posições dos rostos...")

        graph = self._build_video_graph(source_video, start_time, end_time, meta, layout_mode, aspect_ratio)
        graph += ",setsar=1"
        if ass_path and os.path.exists(ass_path):
            escaped_ass = str(Path(ass_path).resolve()).replace("\\", "/").replace(":", r"\:")
            graph += f",ass='{escaped_ass}'"
        graph += "[vout]"

        encoder = encoder or detect_hw_encoder()
        attempts = [encoder] if encoder == "libx264" else [encoder, "libx264"]
        last_err = ""

        for enc in attempts:
            cmd = [
                self.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-ss", f"{start_time:.3f}",
                "-t", f"{duration:.3f}",
                "-i", source_video,
                "-filter_complex", graph,
                "-map", "[vout]", "-map", "0:a:0?",
                *encoder_args(enc),
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                "-movflags", "+faststart",
                str(out_file.resolve())
            ]
            if progress_callback:
                progress_callback(40.0, f"Renderizando clipe ({enc})...")
            logger.info(f"Rendering clip with {enc}: {out_file.name}")
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", creationflags=SUBPROCESS_FLAGS)
            if res.returncode == 0 and out_file.exists() and out_file.stat().st_size > 0:
                if progress_callback:
                    progress_callback(100.0, "Clipe renderizado com sucesso!")
                return str(out_file.resolve())
            last_err = (res.stderr or "").strip()
            logger.warning(f"Render with {enc} failed: {last_err[-600:]}")

        raise RuntimeError(f"Erro na renderização do clipe pelo FFmpeg: {last_err[-400:]}")

    def generate_thumbnail(self, video_path: str, output_thumb: str, at_time: float = 1.0) -> str:
        """Extracts a thumbnail frame from video."""
        out = Path(output_thumb)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-ss", f"{at_time:.2f}",
            "-i", video_path,
            "-frames:v", "1",
            "-vf", "scale=360:-2",
            "-q:v", "3",
            str(out.resolve())
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=SUBPROCESS_FLAGS)
        return str(out.resolve())
