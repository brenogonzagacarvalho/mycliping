import os
import time
import logging
import threading
from typing import Dict, Any, List, Optional, Callable

# Compatibilidade do faster-whisper com versões recentes do PyAV (av >= 18.0.0)
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

from faster_whisper import WhisperModel
from app.config import CACHE_DIR, CPU_THREADS

logger = logging.getLogger("mycliping.transcriber")


class AudioTranscriber:
    _models: Dict[str, WhisperModel] = {}
    _load_lock = threading.Lock()

    @classmethod
    def get_model(cls, model_size: str = "base", device: str = "cpu", compute_type: str = "int8") -> WhisperModel:
        key = f"{model_size}_{device}_{compute_type}"
        with cls._load_lock:  # avoid two jobs loading/downloading the same model at once
            if key not in cls._models:
                logger.info(f"Loading faster-whisper model '{model_size}' ({compute_type}) on {device}...")
                cls._models[key] = WhisperModel(
                    model_size,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=CPU_THREADS,
                    download_root=str((CACHE_DIR / "whisper").resolve())
                )
                logger.info(f"Model '{model_size}' loaded successfully.")
            return cls._models[key]

    def transcribe(
        self,
        audio_path: str,
        model_size: str = "base",
        language: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Transcribes audio with word-level timestamps.
        Language can be 'auto', 'pt', 'en', 'es', etc.
        """
        start_t = time.time()
        if language == "auto" or not language:
            language = None

        if progress_callback:
            progress_callback(5.0, f"Carregando modelo de transcrição Whisper ({model_size})...")

        model = self.get_model(model_size=model_size)

        if progress_callback:
            progress_callback(10.0, "Transcrevendo áudio e identificando cada palavra...")

        segments_gen, info = model.transcribe(
            audio_path,
            language=language,
            word_timestamps=True,
            beam_size=1,            # greedy decoding: ~2-3x faster on CPU, minimal accuracy loss
            vad_filter=True,        # Voice Activity Detection filters out silence
            vad_parameters=dict(min_silence_duration_ms=500),
            condition_on_previous_text=False,  # avoids hallucination loops on long audio
        )

        all_words: List[Dict[str, Any]] = []
        segments_list: List[Dict[str, Any]] = []
        full_text_parts: List[str] = []

        total_duration = info.duration or 1.0
        last_emit = 0.0

        for segment in segments_gen:
            text = segment.text.strip()
            seg_dict = {
                "id": segment.id,
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "text": text,
                "words": []
            }
            full_text_parts.append(text)

            for w in segment.words or []:
                word_obj = {
                    "word": w.word.strip(),
                    "start": round(w.start, 2),
                    "end": round(w.end, 2),
                    "probability": round(w.probability, 2)
                }
                seg_dict["words"].append(word_obj)
                all_words.append(word_obj)

            segments_list.append(seg_dict)

            now = time.time()
            if progress_callback and now - last_emit > 0.7:
                last_emit = now
                elapsed = now - start_t
                ratio = min(1.0, segment.end / total_duration)
                eta = (elapsed / ratio - elapsed) if ratio > 0.02 else 0
                eta_txt = f" • faltam ~{int(eta // 60)}m{int(eta % 60):02d}s" if eta else ""
                progress_callback(
                    min(98.0, 10.0 + ratio * 88.0),
                    f"Transcrevendo: {segment.end:.0f}s / {total_duration:.0f}s{eta_txt}"
                )

        if progress_callback:
            progress_callback(100.0, "Transcrição concluída!")

        elapsed = round(time.time() - start_t, 2)
        logger.info(
            f"Transcription finished in {elapsed}s ({total_duration / max(elapsed, 0.01):.1f}x realtime). "
            f"Detected language: {info.language} ({info.language_probability:.2f})"
        )

        return {
            "language": info.language,
            "language_probability": round(info.language_probability, 2),
            "duration": round(total_duration, 2),
            "full_text": " ".join(full_text_parts),
            "segments": segments_list,
            "words": all_words
        }
