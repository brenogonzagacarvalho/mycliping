import os
import time
import logging
from typing import Dict, Any, List, Optional, Callable
from pathlib import Path

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
from app.config import CACHE_DIR

logger = logging.getLogger("mycliping.transcriber")

class AudioTranscriber:
    _models: Dict[str, WhisperModel] = {}

    @classmethod
    def get_model(cls, model_size: str = "base", device: str = "cpu", compute_type: str = "int8") -> WhisperModel:
        key = f"{model_size}_{device}_{compute_type}"
        if key not in cls._models:
            logger.info(f"Loading faster-whisper model '{model_size}' ({compute_type}) on {device}...")
            # faster-whisper automatically downloads to huggingface cache if not present
            cls._models[key] = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
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
            progress_callback(10.0, f"Carregando modelo de transcrição Whisper ({model_size})...")

        model = self.get_model(model_size=model_size)

        if progress_callback:
            progress_callback(30.0, "Transcrevendo áudio e identificando cada palavra...")

        # Transcribe with word timestamps enabled
        segments_gen, info = model.transcribe(
            audio_path,
            language=language,
            word_timestamps=True,
            beam_size=5,
            vad_filter=True, # Voice Activity Detection filters out silence
            vad_parameters=dict(min_silence_duration_ms=500)
        )

        all_words: List[Dict[str, Any]] = []
        segments_list: List[Dict[str, Any]] = []
        full_text_parts: List[str] = []

        total_duration = info.duration or 1.0

        for segment in segments_gen:
            seg_dict = {
                "id": segment.id,
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "text": segment.text.strip(),
                "words": []
            }
            full_text_parts.append(segment.text.strip())

            if segment.words:
                for w in segment.words:
                    word_obj = {
                        "word": w.word.strip(),
                        "start": round(w.start, 2),
                        "end": round(w.end, 2),
                        "probability": round(w.probability, 2)
                    }
                    seg_dict["words"].append(word_obj)
                    all_words.append(word_obj)

            segments_list.append(seg_dict)

            # Progress estimate based on timestamps
            progress_pct = min(95.0, 30.0 + (segment.end / total_duration) * 65.0)
            if progress_callback:
                progress_callback(
                    progress_pct,
                    f"Transcrevendo: {segment.end:.1f}s / {total_duration:.1f}s ({segment.text.strip()[:35]}...)"
                )

        if progress_callback:
            progress_callback(100.0, "Transcrição concluída com precisão temporal!")

        elapsed = round(time.time() - start_t, 2)
        logger.info(f"Transcription finished in {elapsed}s. Detected language: {info.language} ({info.language_probability:.2f})")

        return {
            "language": info.language,
            "language_probability": round(info.language_probability, 2),
            "duration": round(total_duration, 2),
            "full_text": " ".join(full_text_parts),
            "segments": segments_list,
            "words": all_words
        }
