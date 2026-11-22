import time
import uuid
import shutil
import logging
import asyncio
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from app.config import (
    CACHE_DIR, CLIPS_DIR, UPLOADS_DIR, LIVE_BUFFER_DIR, FFMPEG_PATH, SUBPROCESS_FLAGS,
    CPU_THREADS, load_settings, recommended_concurrent_jobs
)
from app.core.downloader import VideoDownloader
from app.core.transcriber import AudioTranscriber
from app.core.ai_analyzer import AIClipAnalyzer
from app.core.caption_engine import CaptionEngine
from app.core.video_renderer import VideoRenderer, resolve_encoder, HW_ENCODERS
from app.core.storage import clip_storage

logger = logging.getLogger("mycliping.pipeline")


class PipelineTask:
    def __init__(self, task_id: str, title: str):
        self.task_id = task_id
        self.title = title
        self.status = "queued"     # queued, processing, completed, error
        self.stage = "Na fila"
        self.progress = 0.0
        self.message = "Aguardando início..."
        self.clips: List[Dict[str, Any]] = []
        self.error: Optional[str] = None
        self.created_at = time.time()

    def update(self, progress: float, stage: str, message: str):
        self.progress = round(progress, 1)
        self.stage = stage
        self.message = message


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class ClipPipeline:
    def __init__(self):
        self.downloader = VideoDownloader()
        self.transcriber = AudioTranscriber()
        self.ai_analyzer = AIClipAnalyzer()
        self.caption_engine = CaptionEngine()
        self.renderer = VideoRenderer()
        self.tasks: Dict[str, PipelineTask] = {}
        # FIFO job scheduler (limits how many heavy jobs run at the same time)
        self._cond: Optional[asyncio.Condition] = None
        self._waiting: List[str] = []
        self._running = 0

    def get_task(self, task_id: str) -> Optional[PipelineTask]:
        return self.tasks.get(task_id)

    @staticmethod
    def max_jobs() -> int:
        try:
            return max(1, min(8, int(load_settings().get("max_concurrent_jobs") or 1)))
        except (TypeError, ValueError):
            return recommended_concurrent_jobs()

    def queue_stats(self) -> Dict[str, int]:
        return {"running": self._running, "waiting": len(self._waiting), "max_jobs": self.max_jobs()}

    # ------------------------------------------------------------------
    # Async entry point: queues the job and runs the heavy work in a thread
    # so the web server stays responsive (progress, gallery, live, etc.)
    # ------------------------------------------------------------------
    async def process_video_job(
        self,
        task_id: str,
        source_type: str,     # 'url', 'upload', 'live_clip'
        source_target: str,   # URL or local file path
        options: Dict[str, Any],
        ws_callback: Optional[Callable[[Dict[str, Any]], Any]] = None
    ):
        loop = asyncio.get_running_loop()
        task = self.tasks[task_id]

        def notify(progress: float, stage: str, message: str):
            """Thread-safe progress update + WebSocket broadcast."""
            task.update(progress, stage, message)
            if ws_callback:
                payload = {
                    "task_id": task_id,
                    "status": task.status,
                    "progress": task.progress,
                    "stage": task.stage,
                    "message": task.message,
                }
                try:
                    asyncio.run_coroutine_threadsafe(ws_callback(payload), loop)
                except RuntimeError:
                    pass

        if self._cond is None:
            self._cond = asyncio.Condition()

        acquired = False
        try:
            async with self._cond:
                self._waiting.append(task_id)
                while self._running >= self.max_jobs() or self._waiting[0] != task_id:
                    pos = self._waiting.index(task_id) + 1
                    task.status = "queued"
                    notify(0.0, "Na fila",
                           f"Aguardando vaga no processador — posição {pos} na fila "
                           f"({self._running} em andamento, limite {self.max_jobs()}).")
                    await self._cond.wait()
                self._waiting.remove(task_id)
                self._running += 1
                acquired = True
                self._cond.notify_all()  # let the others refresh their queue position

            task.status = "processing"
            await asyncio.to_thread(self._run_job, task, source_type, source_target, options, notify)
        finally:
            async with self._cond:
                if task_id in self._waiting:
                    self._waiting.remove(task_id)
                if acquired:
                    self._running -= 1
                self._cond.notify_all()

    # ------------------------------------------------------------------
    # Synchronous job (runs in a worker thread)
    # ------------------------------------------------------------------
    def _extract_audio(self, video_file: str, audio_out: Path):
        cmd = [
            FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", video_file,
            "-vn", "-sn",
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(audio_out.resolve())
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", creationflags=SUBPROCESS_FLAGS)
        if res.returncode != 0 or not audio_out.exists():
            raise RuntimeError(f"Falha ao extrair o áudio do vídeo: {(res.stderr or '').strip()[-300:]}")

    @staticmethod
    def _render_workers(settings: dict, encoder: str, n_clips: int) -> int:
        try:
            forced = int(settings.get("render_workers") or 0)
        except (TypeError, ValueError):
            forced = 0
        if forced > 0:
            workers = forced
        elif encoder in HW_ENCODERS:
            # GPU encodes; CPU only decodes/filters -> 2 renders in parallel is the sweet spot
            workers = 2 if CPU_THREADS >= 4 else 1
        else:
            # libx264 already uses every thread; parallel renders only help on big CPUs
            workers = max(1, CPU_THREADS // 8)
        return max(1, min(workers, n_clips))

    def _render_single_clip(
        self, idx: int, clip_spec: Dict[str, Any], video_file: str, meta: Dict[str, Any],
        words: List[Dict[str, Any]], task_id: str, video_title: str, source_type: str,
        caption_style: str, layout_mode: str, aspect_ratio: str, burn_title: bool, encoder: str
    ) -> Dict[str, Any]:
        clip_id = f"{task_id}_clip_{idx + 1}_{uuid.uuid4().hex[:6]}"
        clip_dir = CLIPS_DIR / clip_id
        clip_dir.mkdir(parents=True, exist_ok=True)
        try:
            c_start, c_end = clip_spec["start"], clip_spec["end"]
            hook_title = clip_spec.get("hook_title", f"PARTE {idx + 1}")

            ass_file = clip_dir / "subtitles.ass"
            self.caption_engine.generate_ass(
                words=words,
                clip_start=c_start,
                clip_end=c_end,
                output_file=str(ass_file.resolve()),
                style_name=caption_style,
                hook_title=hook_title if burn_title else None,
                video_w=1080 if aspect_ratio != "16:9" else 1920,
                video_h={"9:16": 1920, "1:1": 1080}.get(aspect_ratio, 1080),
            )

            final_video_path = clip_dir / "clip.mp4"
            self.renderer.render_clip(
                source_video=video_file,
                start_time=c_start,
                end_time=c_end,
                output_path=str(final_video_path.resolve()),
                ass_path=str(ass_file.resolve()),
                layout_mode=layout_mode,
                aspect_ratio=aspect_ratio,
                encoder=encoder,
                meta=meta,
            )

            thumb_path = clip_dir / "thumb.jpg"
            self.renderer.generate_thumbnail(
                str(final_video_path.resolve()), str(thumb_path.resolve()),
                at_time=min(1.5, (c_end - c_start) / 2)
            )

            return {
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
        except Exception:
            shutil.rmtree(clip_dir, ignore_errors=True)
            raise

    def _run_job(self, task: PipelineTask, source_type: str, source_target: str,
                 options: Dict[str, Any], notify: Callable[[float, str, str], None]):
        task_id = task.task_id
        settings = load_settings()
        cleanup = bool(settings.get("cleanup_source_after", True))
        audio_out = CACHE_DIR / f"{task_id}_audio.wav"
        source_dir_to_clean: Optional[Path] = None
        source_file_to_clean: Optional[Path] = None

        try:
            whisper_model = options.get("whisper_model") or settings.get("whisper_model", "base")
            whisper_lang = options.get("whisper_language") or settings.get("whisper_language", "auto")
            caption_style = options.get("caption_style") or settings.get("caption_style", "mrbeast")
            layout_mode = options.get("layout_mode") or settings.get("layout_mode", "smart_face")
            aspect_ratio = options.get("aspect_ratio") or settings.get("aspect_ratio", "9:16")
            max_clips = max(1, min(20, int(options.get("max_clips") or settings.get("max_clips", 3))))
            min_dur = int(options.get("min_clip_duration") or settings.get("min_clip_duration", 25))
            max_dur = int(options.get("max_clip_duration") or settings.get("max_clip_duration", 60))
            burn_title = bool(settings.get("burn_hook_title", True))

            # 1. OBTER VÍDEO --------------------------------------------------
            if source_type == "url":
                notify(2.0, "Download", "Iniciando download do vídeo...")
                dl_info = self.downloader.download_video(
                    source_target,
                    output_name=task_id,
                    progress_callback=lambda pct, msg: notify(2.0 + pct * 0.23, "Download", msg)
                )
                video_file = dl_info["file_path"]
                video_title = dl_info["title"]
                source_dir_to_clean = Path(dl_info["target_dir"])
            else:
                video_file = source_target
                video_title = options.get("title") or Path(source_target).stem
                if source_type == "upload" and _inside(Path(source_target), UPLOADS_DIR):
                    source_dir_to_clean = Path(source_target).parent
                elif source_type == "live_clip" and _inside(Path(source_target), LIVE_BUFFER_DIR):
                    source_file_to_clean = Path(source_target)
                notify(25.0, "Preparação", "Vídeo local carregado com sucesso.")

            task.title = video_title
            meta = self.renderer.probe_video(video_file)
            if not meta["has_audio"]:
                raise RuntimeError("O vídeo não possui trilha de áudio, então não é possível transcrever.")

            # 2. ÁUDIO ----------------------------------------------------------
            notify(26.0, "Áudio", "Extraindo trilha de áudio em 16kHz...")
            self._extract_audio(video_file, audio_out)

            # 3. TRANSCRIÇÃO ---------------------------------------------------
            notify(30.0, "Transcrição", "Iniciando transcrição com Whisper...")
            transcript_data = self.transcriber.transcribe(
                str(audio_out.resolve()),
                model_size=whisper_model,
                language=whisper_lang,
                progress_callback=lambda pct, msg: notify(30.0 + pct * 0.30, "Transcrição", msg)
            )

            # 4. ANÁLISE IA ----------------------------------------------------
            notify(61.0, "Análise IA", "IA selecionando momentos virais e calculando scores...")
            selected_clips = self.ai_analyzer.analyze(
                transcript_data=transcript_data,
                max_clips=max_clips,
                min_duration=min_dur,
                max_duration=max_dur,
                audio_path=str(audio_out.resolve()),
                video_duration=meta["duration"] or transcript_data.get("duration"),
            )
            if not selected_clips:
                raise RuntimeError("Nenhum trecho adequado foi encontrado para gerar clipes.")
            logger.info(f"AI selected {len(selected_clips)} viral moments for task {task_id}")

            # 5. RENDERIZAÇÃO --------------------------------------------------
            encoder = resolve_encoder(settings.get("video_encoder", "auto"))
            n = len(selected_clips)
            workers = self._render_workers(settings, encoder, n)
            notify(65.0, "Renderização", f"Renderizando {n} clipes ({encoder}, {workers} em paralelo)...")

            created: List[Dict[str, Any]] = []
            failures: List[str] = []
            done = 0
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"render-{task_id}") as ex:
                futures = {
                    ex.submit(
                        self._render_single_clip, idx, spec, video_file, meta, transcript_data["words"],
                        task_id, video_title, source_type, caption_style, layout_mode, aspect_ratio,
                        burn_title, encoder
                    ): idx
                    for idx, spec in enumerate(selected_clips)
                }
                for fut in as_completed(futures):
                    idx = futures[fut]
                    done += 1
                    try:
                        record = fut.result()
                        clip_storage.save_clip(record)
                        created.append(record)
                    except Exception as e:
                        logger.exception(f"Clip {idx + 1} of task {task_id} failed")
                        failures.append(f"clipe {idx + 1}: {str(e)[:160]}")
                    suffix = f" ({len(failures)} com erro)" if failures else ""
                    notify(65.0 + done / n * 33.0, "Renderização", f"{done}/{n} clipes processados{suffix}")

            if not created:
                raise RuntimeError("Todos os clipes falharam na renderização. " + (failures[0] if failures else ""))

            created.sort(key=lambda c: c.get("virality_score", 0), reverse=True)
            task.clips = created
            task.error = "; ".join(failures) or None
            task.status = "completed"
            if failures:
                notify(100.0, "Concluído", f"{len(created)} de {n} clipes gerados. Falhas: {'; '.join(failures)[:300]}")
            else:
                notify(100.0, "Concluído", f"Sucesso! {len(created)} clipes virais gerados.")

        except Exception as e:
            logger.exception(f"Pipeline error for task {task_id}: {e}")
            task.status = "error"
            task.error = str(e)
            notify(task.progress, "Erro", f"Erro no processamento: {str(e)}")
        finally:
            audio_out.unlink(missing_ok=True)
            if cleanup:
                if source_dir_to_clean and _inside(source_dir_to_clean, UPLOADS_DIR) \
                        and source_dir_to_clean.resolve() != UPLOADS_DIR.resolve():
                    shutil.rmtree(source_dir_to_clean, ignore_errors=True)
                if source_file_to_clean:
                    source_file_to_clean.unlink(missing_ok=True)


pipeline = ClipPipeline()
