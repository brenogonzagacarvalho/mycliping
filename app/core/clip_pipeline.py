import os
import time
import uuid
import logging
import asyncio
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from app.config import CACHE_DIR, CLIPS_DIR, FFMPEG_PATH, load_settings
from app.core.downloader import VideoDownloader
from app.core.transcriber import AudioTranscriber
from app.core.ai_analyzer import AIClipAnalyzer
from app.core.caption_engine import CaptionEngine
from app.core.video_renderer import VideoRenderer
from app.core.storage import clip_storage

logger = logging.getLogger("mycliping.pipeline")

class PipelineTask:
    def __init__(self, task_id: str, title: str):
        self.task_id = task_id
        self.title = title
        self.status = "queued"     # queued, processing, completed, error
        self.stage = "Iniciando"
        self.progress = 0.0
        self.message = "Aguardando início..."
        self.clips: List[Dict[str, Any]] = []
        self.error: Optional[str] = None
        self.created_at = time.time()

    def update(self, progress: float, stage: str, message: str):
        self.progress = round(progress, 1)
        self.stage = stage
        self.message = message

class ClipPipeline:
    def __init__(self):
        self.downloader = VideoDownloader()
        self.transcriber = AudioTranscriber()
        self.ai_analyzer = AIClipAnalyzer()
        self.caption_engine = CaptionEngine()
        self.renderer = VideoRenderer()
        self.tasks: Dict[str, PipelineTask] = {}

    def get_task(self, task_id: str) -> Optional[PipelineTask]:
        return self.tasks.get(task_id)

    async def process_video_job(
        self,
        task_id: str,
        source_type: str,     # 'url', 'upload', 'live_clip'
        source_target: str,   # URL or local file path
        options: Dict[str, Any],
        ws_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        task = self.tasks[task_id]
        task.status = "processing"

        def notify(progress: float, stage: str, message: str):
            task.update(progress, stage, message)
            if ws_callback:
                asyncio.create_task(ws_callback({
                    "task_id": task_id,
                    "status": task.status,
                    "progress": task.progress,
                    "stage": task.stage,
                    "message": task.message
                }))

        try:
            settings = load_settings()
            whisper_model = options.get("whisper_model") or settings.get("whisper_model", "base")
            whisper_lang = options.get("whisper_language") or settings.get("whisper_language", "auto")
            caption_style = options.get("caption_style") or settings.get("caption_style", "mrbeast")
            layout_mode = options.get("layout_mode") or settings.get("layout_mode", "smart_face")
            aspect_ratio = options.get("aspect_ratio") or settings.get("aspect_ratio", "9:16")
            max_clips = int(options.get("max_clips") or settings.get("max_clips", 3))
            min_dur = int(options.get("min_clip_duration") or settings.get("min_clip_duration", 25))
            max_dur = int(options.get("max_clip_duration") or settings.get("max_clip_duration", 60))

            # -----------------------------------------------------------------
            # 1. OBTER VÍDEO
            # -----------------------------------------------------------------
            video_file = ""
            video_title = "Vídeo"

            if source_type == "url":
                notify(5.0, "Download", "Iniciando download do vídeo...")
                dl_info = self.downloader.download_video(
                    source_target,
                    output_name=task_id,
                    progress_callback=lambda pct, msg: notify(5.0 + (pct * 0.20), "Download", msg)
                )
                video_file = dl_info["file_path"]
                video_title = dl_info["title"]
            else:
                # Local upload or Live extracted clip
                video_file = source_target
                video_title = options.get("title", Path(source_target).stem)
                notify(25.0, "Preparação", "Vídeo local carregado com sucesso.")

            task.title = video_title

            # -----------------------------------------------------------------
            # 2. EXTRAIR ÁUDIO PARA TRANSCRIÇÃO
            # -----------------------------------------------------------------
            notify(28.0, "Áudio", "Extraindo trilha de áudio em 16kHz...")
            audio_out = CACHE_DIR / f"{task_id}_audio.wav"
            cmd_audio = [
                FFMPEG_PATH,
                "-y",
                "-i", video_file,
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                str(audio_out.resolve())
            ]
            subprocess.run(cmd_audio, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            # -----------------------------------------------------------------
            # 3. TRANSCRIÇÃO COM WHISPER (PALAVRA POR PALAVRA)
            # -----------------------------------------------------------------
            notify(32.0, "Transcrição", "Iniciando transcrição com Whisper...")
            transcript_data = self.transcriber.transcribe(
                str(audio_out.resolve()),
                model_size=whisper_model,
                language=whisper_lang,
                progress_callback=lambda pct, msg: notify(32.0 + (pct * 0.28), "Transcrição", msg)
            )

            # -----------------------------------------------------------------
            # 4. ANÁLISE DE MOMENTOS VIRAIS PELA IA
            # -----------------------------------------------------------------
            notify(62.0, "Análise IA", "IA selecionando momentos virais e calculando scores...")
            selected_clips = self.ai_analyzer.analyze(
                transcript_data=transcript_data,
                max_clips=max_clips,
                min_duration=min_dur,
                max_duration=max_dur,
                audio_path=str(audio_out.resolve())
            )

            logger.info(f"AI selected {len(selected_clips)} viral moments for task {task_id}")

            # -----------------------------------------------------------------
            # 5. RENDERIZAÇÃO DE CADA CLIPE EM 9:16 COM LEGENDAS ANIMADAS
            # -----------------------------------------------------------------
            created_clips = []
            total_clips_to_render = len(selected_clips)

            for idx, clip_spec in enumerate(selected_clips):
                clip_id = f"{task_id}_clip_{idx+1}_{uuid.uuid4().hex[:6]}"
                clip_dir = CLIPS_DIR / clip_id
                clip_dir.mkdir(parents=True, exist_ok=True)

                c_start = clip_spec["start"]
                c_end = clip_spec["end"]
                hook_title = clip_spec.get("hook_title", f"PARTE {idx+1}")

                progress_base = 65.0 + ((idx / max(1, total_clips_to_render)) * 30.0)
                notify(progress_base, "Renderização", f"Gerando clipe {idx+1}/{total_clips_to_render}: '{hook_title}'...")

                # a) Gerar legendas animadas (.ass)
                ass_file = clip_dir / "subtitles.ass"
                self.caption_engine.generate_ass(
                    words=transcript_data["words"],
                    clip_start=c_start,
                    clip_end=c_end,
                    output_file=str(ass_file.resolve()),
                    style_name=caption_style,
                    hook_title=hook_title if settings.get("burn_hook_title", True) else None
                )

                # b) Renderizar vídeo com recorte 9:16 e queima das legendas
                final_video_path = clip_dir / "clip.mp4"
                self.renderer.render_clip(
                    source_video=video_file,
                    start_time=c_start,
                    end_time=c_end,
                    output_path=str(final_video_path.resolve()),
                    ass_path=str(ass_file.resolve()),
                    layout_mode=layout_mode,
                    aspect_ratio=aspect_ratio
                )

                # c) Gerar Thumbnail
                thumb_path = clip_dir / "thumb.jpg"
                self.renderer.generate_thumbnail(
                    str(final_video_path.resolve()),
                    str(thumb_path.resolve()),
                    at_time=min(1.5, (c_end - c_start) / 2)
                )

                clip_record = {
                    "id": clip_id,
                    "task_id": task_id,
                    "source_title": video_title,
                    "hook_title": hook_title,
                    "duration": round(c_end - c_start, 1),
                    "start_time": c_start,
                    "end_time": c_end,
                    "virality_score": clip_spec.get("virality_score", 90),
                    "hook_score": clip_spec.get("hook_score", 92),
                    "retention_score": clip_spec.get("retention_score", 89),
                    "shareability_score": clip_spec.get("shareability_score", 91),
                    "summary": clip_spec.get("summary", ""),
                    "hashtags": clip_spec.get("hashtags", ["#viral", "#shorts"]),
                    "caption_style": caption_style,
                    "layout_mode": layout_mode,
                    "video_path": str(final_video_path.resolve()),
                    "thumb_path": str(thumb_path.resolve()),
                    "created_at": time.time(),
                    "is_live": source_type == "live_clip"
                }

                # Salvar na galeria permanente
                clip_storage.save_clip(clip_record)
                created_clips.append(clip_record)

            task.clips = created_clips
            task.status = "completed"
            notify(100.0, "Concluído", f"Sucesso! {len(created_clips)} clipes virais gerados com perfeição.")

        except Exception as e:
            logger.exception(f"Pipeline error for task {task_id}: {e}")
            task.status = "error"
            task.error = str(e)
            notify(task.progress, "Erro", f"Erro no processamento: {str(e)}")

pipeline = ClipPipeline()
