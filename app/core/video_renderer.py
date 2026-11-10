import os
import sys
import subprocess
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Callable
import cv2
from app.config import FFMPEG_PATH
from app.core.face_tracker import FaceTracker

logger = logging.getLogger("mycliping.renderer")

class VideoRenderer:
    def __init__(self):
        self.ffmpeg_path = FFMPEG_PATH
        self.face_tracker = FaceTracker()

    def probe_video(self, video_path: str) -> Dict[str, Any]:
        """Probes video dimensions, duration, and fps using OpenCV."""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Não foi possível abrir o vídeo para leitura: {video_path}")

        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 else 0.0

        cap.release()
        return {
            "width": w,
            "height": h,
            "fps": fps,
            "duration": duration
        }

    def render_clip(
        self,
        source_video: str,
        start_time: float,
        end_time: float,
        output_path: str,
        ass_path: Optional[str] = None,
        layout_mode: str = "smart_face",
        aspect_ratio: str = "9:16",
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> str:
        """
        Trims, reframes (9:16), burns subtitles, and renders the final MP4.
        """
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        meta = self.probe_video(source_video)
        src_w, src_h = meta["width"], meta["height"]
        duration = max(0.5, end_time - start_time)

        if progress_callback:
            progress_callback(10.0, "Analisando enquadramento e posições dos rostos...")

        # 1. Build video filter graph based on layout mode
        filters = []

        if aspect_ratio == "9:16":
            target_w, target_h = 1080, 1920

            if layout_mode == "smart_face":
                # Detect speaker face position
                detections = self.face_tracker.analyze_face_positions(source_video, start_time, end_time)
                crop_center_x = self.face_tracker.compute_smoothed_crop_x(detections, src_w, src_h)

                crop_w_src = int(src_h * (9 / 16))
                if crop_w_src % 2 != 0:
                    crop_w_src -= 1
                crop_x = int(max(0, min(src_w - crop_w_src, crop_center_x - crop_w_src / 2)))

                filter_chain = f"crop={crop_w_src}:{src_h}:{crop_x}:0,scale={target_w}:{target_h}"
                filters.append(filter_chain)

            elif layout_mode == "split_screen":
                # Top: Zoomed facecam (40%), Bottom: Gameplay/Content (60%)
                top_h = 768
                bot_h = 1152
                filter_chain = (
                    f"[0:v]split=2[cam][game];"
                    f"[cam]crop=ih*0.56:ih:iw*0.25:0,scale={target_w}:{top_h}[top];"
                    f"[game]scale={target_w}:{bot_h}:force_original_aspect_ratio=decrease,pad={target_w}:{bot_h}:(ow-iw)/2:(oh-ih)/2[bot];"
                    f"[top][bot]vstack=inputs=2"
                )
                filters.append(filter_chain)

            elif layout_mode == "blurred_bg":
                # Blurred canvas in background with centered crisp video
                filter_chain = (
                    f"[0:v]split=2[bg][fg];"
                    f"[bg]scale={target_w}:{target_h},boxblur=20:5[blurred];"
                    f"[fg]scale={target_w}:-1[sharp];"
                    f"[blurred][sharp]overlay=(W-w)/2:(H-h)/2"
                )
                filters.append(filter_chain)

            else:
                # Default center crop to 9:16
                crop_w_src = int(src_h * (9 / 16))
                crop_x = (src_w - crop_w_src) // 2
                filters.append(f"crop={crop_w_src}:{src_h}:{crop_x}:0,scale={target_w}:{target_h}")

        elif aspect_ratio == "1:1":
            crop_size = min(src_w, src_h)
            crop_x = (src_w - crop_size) // 2
            crop_y = (src_h - crop_size) // 2
            filters.append(f"crop={crop_size}:{crop_size}:{crop_x}:{crop_y},scale=1080:1080")

        else: # 16:9
            filters.append("scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2")

        # 2. Append ASS subtitle filter if provided
        if ass_path and os.path.exists(ass_path):
            escaped_ass = str(Path(ass_path).resolve()).replace("\\", "/").replace(":", r"\:")
            if filters and ("vstack" in filters[0] or "overlay" in filters[0]):
                # Complex filtergraph needs subtitle attached at output
                vf_arg = f"{filters[0]},ass='{escaped_ass}'"
                filter_opt = ["-filter_complex", vf_arg]
            else:
                filters.append(f"ass='{escaped_ass}'")
                filter_opt = ["-vf", ",".join(filters)]
        else:
            if filters and ("vstack" in filters[0] or "overlay" in filters[0]):
                filter_opt = ["-filter_complex", filters[0]]
            else:
                filter_opt = ["-vf", ",".join(filters)] if filters else []

        if progress_callback:
            progress_callback(40.0, "Renderizando clipe em alta definição (H.264 / AAC)...")

        # 3. Assemble FFmpeg render command
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-ss", f"{start_time:.3f}",
            "-t", f"{duration:.3f}",
            "-i", source_video
        ]
        cmd.extend(filter_opt)
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "21",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-movflags", "+faststart",
            str(out_file.resolve())
        ])

        logger.info(f"Rendering clip: {cmd}")
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creationflags
        )
        _, stderr = proc.communicate()

        if proc.returncode != 0 or not out_file.exists():
            logger.error(f"FFmpeg render error: {stderr}")
            raise RuntimeError(f"Erro na renderização do clipe pelo FFmpeg: {stderr[-400:]}")

        if progress_callback:
            progress_callback(100.0, "Clipe renderizado com sucesso!")

        logger.info(f"Clip successfully created at {out_file}")
        return str(out_file.resolve())

    def generate_thumbnail(self, video_path: str, output_thumb: str, at_time: float = 1.0) -> str:
        """Extracts a thumbnail frame from video."""
        out = Path(output_thumb)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-ss", f"{at_time:.2f}",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            str(out.resolve())
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return str(out.resolve())
