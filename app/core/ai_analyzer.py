import json
import re
import math
import wave
import logging
from typing import List, Dict, Any, Optional
import requests
import numpy as np
from app.config import load_settings

logger = logging.getLogger("mycliping.ai_analyzer")

VIRAL_PROMPT = """Você é um especialista mundial em edição de Shorts, TikTok e Reels virais (estilo OpusClip, MrBeast, Alex Hormozi).
Sua missão é analisar a transcrição de um vídeo/live abaixo e selecionar os melhores momentos ({max_clips} clipes) que têm o maior potencial de viralizar.

Critérios para um clipe perfeito:
1. Gancho forte nos primeiros 3 segundos (desperta curiosidade irresistível).
2. Duração ideal entre {min_duration} e {max_duration} segundos.
3. Não corte frases pela metade: o clipe deve começar no início de um pensamento e terminar na conclusão de um raciocínio ou soco narrativo.
4. Título chamativo (Hook Title) em letras MAIÚSCULAS com até 6 palavras.
5. Os clipes não podem se sobrepor. "start" e "end" são números em segundos, dentro da duração do vídeo ({total_duration:.0f}s).

TRANSCRIÇÃO DO VÍDEO (com timestamps em segundos):
{formatted_transcript}

Retorne ESTRITAMENTE JSON puro (sem markdown). Formato: um objeto {{"clips": [...]}} onde cada item é:
{{
  "start": 14.5,
  "end": 52.0,
  "hook_title": "ELE FEZ ISSO AO VIVO?!",
  "virality_score": 96,
  "hook_score": 98,
  "retention_score": 94,
  "shareability_score": 95,
  "summary": "Momento de alta tensão onde acontece a revelação principal.",
  "hashtags": ["#viral", "#foryou", "#momentos", "#shorts"]
}}
"""

VIRAL_KEYWORDS = {
    "olha", "veja", "inacreditável", "segredo", "nunca", "sempre", "dinheiro",
    "resultado", "história", "aconteceu", "momento", "loucura", "absurdo",
    "verdade", "descobri", "então", "porque", "caraca", "nossa", "mano", "vei",
    "uau", "insano", "erro", "ganhar", "perder", "como", "qual", "melhor",
    "pior", "ninguém", "todo mundo", "sério", "gente", "cara",
}


def _to_float(v) -> Optional[float]:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _to_score(v, default: int) -> int:
    f = _to_float(v)
    return int(max(0, min(100, f))) if f is not None else default


class AIClipAnalyzer:
    def analyze(
        self,
        transcript_data: Dict[str, Any],
        max_clips: int = 3,
        min_duration: int = 25,
        max_duration: int = 60,
        audio_path: Optional[str] = None,
        video_duration: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        settings = load_settings()
        provider = settings.get("ai_provider", "heuristic").lower()
        total = float(video_duration or transcript_data.get("duration") or 0)
        if max_duration < min_duration:
            min_duration, max_duration = max_duration, min_duration

        logger.info(f"Analyzing transcript for viral clips using provider: {provider}")

        if provider != "heuristic" and transcript_data.get("segments"):
            try:
                handlers = {
                    "ollama": self._analyze_with_ollama,
                    "gemini": self._analyze_with_gemini,
                    "openai": self._analyze_with_openai,
                    "groq": self._analyze_with_groq,
                }
                handler = handlers.get(provider)
                if handler:
                    raw = handler(transcript_data, settings, max_clips, min_duration, max_duration, total)
                    clips = self._validate_clips(raw, transcript_data, max_clips, min_duration, max_duration, total)
                    if clips:
                        return clips
                    logger.warning(f"Provider {provider} returned no valid clips. Using heuristic.")
            except Exception as e:
                logger.warning(f"Provider {provider} failed: {e}. Falling back to Smart Audio/Text Heuristic.")

        clips = self._analyze_with_heuristic(transcript_data, max_clips, min_duration, max_duration, audio_path, total)
        return self._validate_clips(clips, transcript_data, max_clips, min_duration, max_duration, total)

    # ------------------------------------------------------------------
    # Validation / normalization of any provider output
    # ------------------------------------------------------------------
    def _snap(self, start: float, end: float, segments: List[Dict[str, Any]], max_dur: float):
        """Expands start/end to sentence (segment) boundaries when it stays within limits."""
        if not segments:
            return start, end
        new_start, new_end = start, end
        for s in segments:
            if s["start"] <= start < s["end"]:
                new_start = s["start"]
                break
        for s in segments:
            if s["start"] < end <= s["end"] + 0.01:
                new_end = s["end"]
                break
        if new_end - new_start <= max_dur * 1.1:
            return new_start, new_end
        return start, end

    def _validate_clips(
        self, clips, transcript_data, max_clips, min_dur, max_dur, total
    ) -> List[Dict[str, Any]]:
        segments = transcript_data.get("segments", [])
        if total <= 0:
            total = float(transcript_data.get("duration") or 0)
        out: List[Dict[str, Any]] = []

        for idx, c in enumerate(clips or []):
            if not isinstance(c, dict):
                continue
            s, e = _to_float(c.get("start")), _to_float(c.get("end"))
            if s is None or e is None:
                continue
            if total > 0:
                s, e = max(0.0, min(s, total)), max(0.0, min(e, total))
            if e <= s:
                continue

            s, e = self._snap(s, e, segments, max_dur)
            if e - s > max_dur * 1.15:
                e = s + max_dur
            if e - s < min_dur * 0.6:
                target_end = s + min_dur
                e = min(target_end, total) if total > 0 else target_end
                if e - s < min_dur * 0.6:
                    s = max(0.0, e - min_dur)
            if e - s < 3.0:
                continue

            title = str(c.get("hook_title") or "").strip() or f"PARTE {idx + 1}"
            hashtags = c.get("hashtags")
            if not isinstance(hashtags, list) or not hashtags:
                hashtags = ["#viral", "#shorts", "#clipe"]

            virality = _to_score(c.get("virality_score"), 85)
            out.append({
                "start": round(s, 2),
                "end": round(e, 2),
                "hook_title": title.upper()[:60],
                "virality_score": virality,
                "hook_score": _to_score(c.get("hook_score"), virality),
                "retention_score": _to_score(c.get("retention_score"), virality),
                "shareability_score": _to_score(c.get("shareability_score"), virality),
                "summary": str(c.get("summary") or ""),
                "hashtags": [str(h) for h in hashtags][:8],
            })

        # Remove overlapping clips, keeping the highest scores
        out.sort(key=lambda x: x["virality_score"], reverse=True)
        final: List[Dict[str, Any]] = []
        for c in out:
            overlap = any(min(c["end"], f["end"]) - max(c["start"], f["start"]) > 0.3 * (c["end"] - c["start"]) for f in final)
            if not overlap:
                final.append(c)
            if len(final) >= max_clips:
                break
        return final

    # ------------------------------------------------------------------
    # LLM providers
    # ------------------------------------------------------------------
    def _format_transcript_for_prompt(self, segments: List[Dict[str, Any]]) -> str:
        return "\n".join(f"[{s['start']:.0f}-{s['end']:.0f}] {s['text']}" for s in segments)

    def _build_prompt(self, transcript_data, max_clips, min_dur, max_dur, total) -> str:
        return VIRAL_PROMPT.format(
            max_clips=max_clips,
            min_duration=min_dur,
            max_duration=max_dur,
            total_duration=total or transcript_data.get("duration", 0),
            formatted_transcript=self._format_transcript_for_prompt(transcript_data["segments"])
        )

    def _clean_json_response(self, text: str) -> List[Dict[str, Any]]:
        """Parses JSON (array or object wrapping an array) from an LLM response."""
        text = (text or "").strip()
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
            if not match:
                return []
            data = json.loads(match.group(1))

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return v
            if "start" in data and "end" in data:
                return [data]
        return []

    def _analyze_with_ollama(self, transcript_data, settings, max_clips, min_dur, max_dur, total):
        url = settings.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/generate"
        model = settings.get("ollama_model", "llama3.2:latest")
        prompt = self._build_prompt(transcript_data, max_clips, min_dur, max_dur, total)
        # Default Ollama context (2-4k tokens) silently truncates long transcripts
        num_ctx = int(min(32768, max(4096, len(prompt) // 3 + 1500)))
        res = requests.post(
            url,
            json={"model": model, "prompt": prompt, "stream": False, "format": "json",
                  "options": {"num_ctx": num_ctx, "temperature": 0.4}},
            timeout=600
        )
        res.raise_for_status()
        return self._clean_json_response(res.json().get("response", "[]"))

    def _analyze_with_gemini(self, transcript_data, settings, max_clips, min_dur, max_dur, total):
        api_key = settings.get("gemini_api_key", "").strip()
        if not api_key:
            raise ValueError("Chave de API do Gemini não configurada.")
        model = settings.get("gemini_model", "gemini-2.5-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": self._build_prompt(transcript_data, max_clips, min_dur, max_dur, total)}]}],
            "generationConfig": {"temperature": 0.4, "response_mime_type": "application/json"}
        }
        res = requests.post(url, json=payload, headers={"x-goog-api-key": api_key}, timeout=180)
        res.raise_for_status()
        raw = res.json()["candidates"][0]["content"]["parts"][0]["text"]
        return self._clean_json_response(raw)

    def _analyze_with_openai(self, transcript_data, settings, max_clips, min_dur, max_dur, total):
        api_key = settings.get("openai_api_key", "").strip()
        if not api_key:
            raise ValueError("Chave de API da OpenAI não configurada.")
        payload = {
            "model": settings.get("openai_model", "gpt-4o-mini"),
            "messages": [{"role": "user", "content": self._build_prompt(transcript_data, max_clips, min_dur, max_dur, total)}],
            "response_format": {"type": "json_object"}
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        res = requests.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers, timeout=180)
        res.raise_for_status()
        return self._clean_json_response(res.json()["choices"][0]["message"]["content"])

    def _analyze_with_groq(self, transcript_data, settings, max_clips, min_dur, max_dur, total):
        api_key = settings.get("groq_api_key", "").strip()
        if not api_key:
            raise ValueError("Chave de API do Groq não configurada.")
        payload = {
            "model": settings.get("groq_model", "llama-3.3-70b-versatile"),
            "messages": [{"role": "user", "content": self._build_prompt(transcript_data, max_clips, min_dur, max_dur, total)}],
            "response_format": {"type": "json_object"},
            "temperature": 0.4,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=120)
        res.raise_for_status()
        return self._clean_json_response(res.json()["choices"][0]["message"]["content"])

    # ------------------------------------------------------------------
    # Offline heuristic (speech rate + keywords + audio energy + hook)
    # ------------------------------------------------------------------
    def _audio_energy_per_second(self, audio_path: Optional[str]) -> Optional[np.ndarray]:
        """RMS loudness per second, normalized by the median (1.0 = typical loudness)."""
        if not audio_path:
            return None
        try:
            with wave.open(audio_path, "rb") as wf:
                if wf.getsampwidth() != 2:
                    return None
                sr, ch = wf.getframerate(), wf.getnchannels()
                values = []
                while True:
                    raw = wf.readframes(sr)
                    if not raw:
                        break
                    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                    if ch > 1:
                        a = a.reshape(-1, ch).mean(axis=1)
                    values.append(float(np.sqrt(np.mean(a * a))) if a.size else 0.0)
            arr = np.array(values, dtype=np.float32)
            nonzero = arr[arr > 0]
            if nonzero.size == 0:
                return None
            return arr / max(float(np.median(nonzero)), 1e-6)
        except Exception as e:
            logger.warning(f"Audio energy analysis failed: {e}")
            return None

    def _analyze_with_heuristic(
        self,
        transcript_data: Dict[str, Any],
        max_clips: int = 3,
        min_duration: int = 25,
        max_duration: int = 60,
        audio_path: Optional[str] = None,
        total: float = 0.0,
    ) -> List[Dict[str, Any]]:
        segments = transcript_data.get("segments", [])
        total_duration = total or transcript_data.get("duration", 0)

        # Short video: a single clip spanning the whole duration
        if total_duration <= max_duration:
            return [{
                "start": 0.0,
                "end": round(total_duration, 1),
                "hook_title": "DESTAQUE IMPERDÍVEL!",
                "virality_score": 92,
                "summary": "Melhor momento selecionado automaticamente.",
                "hashtags": ["#viral", "#shorts", "#clipe"]
            }]

        energy = self._audio_energy_per_second(audio_path)
        candidates = []
        n_segs = len(segments)

        for i in range(n_segs):
            win_start = segments[i]["start"]
            first_text = segments[i]["text"].lower()
            hook_bonus = 0.0
            if "?" in first_text or "!" in first_text:
                hook_bonus += 4
            if any(k in first_text for k in VIRAL_KEYWORDS):
                hook_bonus += 3

            for j in range(i, n_segs):
                end_seg = segments[j]
                dur = end_seg["end"] - win_start
                if dur < min_duration:
                    continue
                if dur > max_duration:
                    break

                window_text = " ".join(s["text"] for s in segments[i:j + 1]).lower()
                words = window_text.split()
                if not words:
                    continue

                wps = len(words) / dur
                kw_hits = sum(1 for w in words if any(k in w for k in VIRAL_KEYWORDS))
                punctuation_bonus = window_text.count("?") * 3 + window_text.count("!") * 2
                rate_score = max(0, 10 - abs(wps - 3.2) * 4)  # energetic speech ~2.5-3.8 words/s
                ending_bonus = 2 if end_seg["text"].rstrip().endswith((".", "!", "?")) else 0

                energy_score = 0.0
                if energy is not None and energy.size:
                    win = energy[int(win_start):max(int(win_start) + 1, int(end_seg["end"]))]
                    if win.size:
                        energy_score = float(np.clip((win.mean() - 1.0) * 8, -5, 10))
                        energy_score += float(np.clip((np.percentile(win, 95) - 1.6) * 3, 0, 8))

                # If user wants many clips on a short video, avoid starving slots with oversized windows
                dur_bonus = (dur / 25.0) if max_clips >= 5 else (dur / 10.0)
                total_score = (rate_score * 4 + kw_hits * 3 + punctuation_bonus + hook_bonus
                               + ending_bonus + energy_score + dur_bonus)

                candidates.append({
                    "start": round(win_start, 2),
                    "end": round(end_seg["end"], 2),
                    "score": total_score,
                    "raw_text": " ".join(s["text"] for s in segments[i:j + 1])
                })

        candidates.sort(key=lambda x: x["score"], reverse=True)

        selected = []
        for cand in candidates:
            if all(cand["end"] <= s["start"] or cand["start"] >= s["end"] for s in selected):
                selected.append(cand)
            if len(selected) >= max_clips:
                break

        # Very quiet / no speech: linear chunks
        if not selected:
            step = max_duration
            for t in range(0, int(total_duration), int(step)):
                end_t = min(total_duration, t + step)
                if end_t - t >= min_duration:
                    selected.append({"start": float(t), "end": float(end_t), "score": 30, "raw_text": ""})
                if len(selected) >= max_clips:
                    break

        hook_titles = [
            "O MOMENTO QUE CHOCOU A TODOS!",
            "VOCÊ NUNCA IMAGINOU ISSO!",
            "A VERDADE POR TRÁS DISSO!",
            "MOMENTO LENDÁRIO NA TRANSMISSÃO!",
            "ELE DISSE ISSO MESMO?!"
        ]

        max_score = max((c.get("score", 0) for c in selected), default=1) or 1
        final_clips = []
        for idx, item in enumerate(selected):
            base_score = int(78 + 20 * max(0.0, item.get("score", 0)) / max_score)
            title = hook_titles[idx % len(hook_titles)]
            first_words = re.sub(r"[^\w\sÀ-ÿ?!]", "", item.get("raw_text", "")).split()[:6]
            if len(first_words) >= 3:
                cand_title = " ".join(first_words).upper().rstrip("?!.,") + "!"
                if len(cand_title) <= 45:
                    title = cand_title

            final_clips.append({
                "start": item["start"],
                "end": item["end"],
                "hook_title": title,
                "virality_score": min(98, base_score),
                "hook_score": min(99, base_score + 2),
                "retention_score": min(99, base_score - 1),
                "shareability_score": min(99, base_score + 1),
                "summary": f"Trecho com alta taxa de engajamento ({item['end'] - item['start']:.1f}s).",
                "hashtags": ["#viral", "#shorts", "#clipe", "#reels"]
            })

        return final_clips
