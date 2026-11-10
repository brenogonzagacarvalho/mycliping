import json
import re
import logging
import math
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

TRANSCRIÇÃO DO VÍDEO (com timestamps em segundos):
{formatted_transcript}

Retorne ESTRITAMENTE um array JSON no seguinte formato (sem markdown extra, apenas JSON puro):
[
  {{
    "start": 14.5,
    "end": 52.0,
    "hook_title": "ELE FEZ ISSO AO VIVO?! 😱",
    "virality_score": 96,
    "hook_score": 98,
    "retention_score": 94,
    "shareability_score": 95,
    "summary": "Momento de alta tensão onde acontece a revelação principal.",
    "hashtags": ["#viral", "#foryou", "#momentos", "#shorts"]
  }}
]
"""

class AIClipAnalyzer:
    def __init__(self):
        pass

    def analyze(
        self,
        transcript_data: Dict[str, Any],
        max_clips: int = 3,
        min_duration: int = 25,
        max_duration: int = 60,
        audio_path: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        settings = load_settings()
        provider = settings.get("ai_provider", "heuristic").lower()

        logger.info(f"Analyzing transcript for viral clips using provider: {provider}")

        # Try chosen provider, fallback to heuristic if needed
        try:
            if provider == "ollama":
                clips = self._analyze_with_ollama(transcript_data, settings, max_clips, min_duration, max_duration)
            elif provider == "gemini":
                clips = self._analyze_with_gemini(transcript_data, settings, max_clips, min_duration, max_duration)
            elif provider == "openai":
                clips = self._analyze_with_openai(transcript_data, settings, max_clips, min_duration, max_duration)
            elif provider == "groq":
                clips = self._analyze_with_groq(transcript_data, settings, max_clips, min_duration, max_duration)
            else:
                clips = self._analyze_with_heuristic(transcript_data, max_clips, min_duration, max_duration)

            if clips:
                return clips
        except Exception as e:
            logger.warning(f"Provider {provider} failed: {e}. Falling back to Smart Audio/Text Heuristic.")

        return self._analyze_with_heuristic(transcript_data, max_clips, min_duration, max_duration)

    def _format_transcript_for_prompt(self, segments: List[Dict[str, Any]]) -> str:
        lines = []
        for s in segments:
            lines.append(f"[{s['start']:.1f}s - {s['end']:.1f}s] {s['text']}")
        return "\n".join(lines)

    def _clean_json_response(self, text: str) -> List[Dict[str, Any]]:
        """Cleans and parses JSON from LLM response."""
        text = text.strip()
        # Remove ```json ... ``` codeblocks if present
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
            else:
                # Try finding any bracketed array
                match_arr = re.search(r"(\[.*\])", text, re.DOTALL)
                if match_arr:
                    text = match_arr.group(1)
        data = json.loads(text)
        if isinstance(data, list):
            return data
        return []

    def _analyze_with_ollama(self, transcript_data: Dict[str, Any], settings: dict, max_clips: int, min_dur: int, max_dur: int) -> List[Dict[str, Any]]:
        url = settings.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/generate"
        model = settings.get("ollama_model", "llama3.2:latest")
        prompt = VIRAL_PROMPT.format(
            max_clips=max_clips,
            min_duration=min_dur,
            max_duration=max_dur,
            formatted_transcript=self._format_transcript_for_prompt(transcript_data["segments"])
        )
        res = requests.post(url, json={"model": model, "prompt": prompt, "stream": False, "format": "json"}, timeout=120)
        res.raise_for_status()
        raw = res.json().get("response", "[]")
        return self._clean_json_response(raw)

    def _analyze_with_gemini(self, transcript_data: Dict[str, Any], settings: dict, max_clips: int, min_dur: int, max_dur: int) -> List[Dict[str, Any]]:
        api_key = settings.get("gemini_api_key", "").strip()
        if not api_key:
            raise ValueError("Chave de API do Gemini não configurada.")
        model = settings.get("gemini_model", "gemini-1.5-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        prompt = VIRAL_PROMPT.format(
            max_clips=max_clips,
            min_duration=min_dur,
            max_duration=max_dur,
            formatted_transcript=self._format_transcript_for_prompt(transcript_data["segments"])
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "response_mime_type": "application/json"}
        }
        res = requests.post(url, json=payload, timeout=60)
        res.raise_for_status()
        data = res.json()
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        return self._clean_json_response(raw)

    def _analyze_with_openai(self, transcript_data: Dict[str, Any], settings: dict, max_clips: int, min_dur: int, max_dur: int) -> List[Dict[str, Any]]:
        api_key = settings.get("openai_api_key", "").strip()
        if not api_key:
            raise ValueError("Chave de API da OpenAI não configurada.")
        model = settings.get("openai_model", "gpt-4o-mini")
        url = "https://api.openai.com/v1/chat/completions"
        prompt = VIRAL_PROMPT.format(
            max_clips=max_clips,
            min_duration=min_dur,
            max_duration=max_dur,
            formatted_transcript=self._format_transcript_for_prompt(transcript_data["segments"])
        )
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        }
        res = requests.post(url, json=payload, headers=headers, timeout=60)
        res.raise_for_status()
        raw = res.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        # If wrapped in an object like {"clips": [...]}
        for k, v in parsed.items():
            if isinstance(v, list):
                return v
        return []

    def _analyze_with_groq(self, transcript_data: Dict[str, Any], settings: dict, max_clips: int, min_dur: int, max_dur: int) -> List[Dict[str, Any]]:
        api_key = settings.get("groq_api_key", "").strip()
        if not api_key:
            raise ValueError("Chave de API do Groq não configurada.")
        model = settings.get("groq_model", "llama-3.3-70b-versatile")
        url = "https://api.groq.com/openai/v1/chat/completions"
        prompt = VIRAL_PROMPT.format(
            max_clips=max_clips,
            min_duration=min_dur,
            max_duration=max_dur,
            formatted_transcript=self._format_transcript_for_prompt(transcript_data["segments"])
        )
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}]
        }
        res = requests.post(url, json=payload, headers=headers, timeout=60)
        res.raise_for_status()
        raw = res.json()["choices"][0]["message"]["content"]
        return self._clean_json_response(raw)

    def _analyze_with_heuristic(
        self,
        transcript_data: Dict[str, Any],
        max_clips: int = 3,
        min_duration: int = 25,
        max_duration: int = 60
    ) -> List[Dict[str, Any]]:
        """
        High-precision offline heuristic algorithm that scores transcript windows
        based on speech rate, engagement words, emotional intensity, questions, and sentence continuity.
        """
        segments = transcript_data.get("segments", [])
        total_duration = transcript_data.get("duration", 0)

        # If transcript is very short, return single clip spanning the duration
        if total_duration <= max_duration:
            return [{
                "start": 0.0,
                "end": round(total_duration, 1),
                "hook_title": "DESTAQUE IMPERDÍVEL! 🔥",
                "virality_score": 92,
                "hook_score": 90,
                "retention_score": 93,
                "shareability_score": 92,
                "summary": "Melhor momento selecionado automaticamente.",
                "hashtags": ["#viral", "#shorts", "#clipe"]
            }]

        viral_keywords = {
            "olha", "veja", "inacreditável", "segredo", "nunca", "sempre", "dinheiro",
            "resultado", "história", "aconteceu", "momento", "loucura", "absurdo",
            "verdade", "descobri", "então", "porque", "caraca", "nossa", "mano", "vei",
            "uau", "insano", "erro", "ganhar", "perder", "como", "qual", "melhor"
        }

        # Candidate windows
        candidates = []
        n_segs = len(segments)

        for i in range(n_segs):
            start_seg = segments[i]
            win_start = start_seg["start"]
            
            # Find matching ending segment within [min_duration, max_duration]
            for j in range(i, n_segs):
                end_seg = segments[j]
                dur = end_seg["end"] - win_start
                if dur < min_duration:
                    continue
                if dur > max_duration:
                    break

                # Score this window
                window_text = " ".join([s["text"] for s in segments[i:j+1]]).lower()
                words = window_text.split()
                w_count = len(words)
                if w_count == 0:
                    continue

                wps = w_count / dur  # Words per second (speech density)
                
                # Bonus for keywords
                kw_hits = sum(1 for w in words if any(k in w for k in viral_keywords))
                
                # Bonus for question marks / exclamation
                punctuation_bonus = window_text.count("?") * 3 + window_text.count("!") * 2
                
                # Score formula
                # Optimal speech rate is 2.5 - 3.8 words/sec (energetic speech)
                rate_score = max(0, 10 - abs(wps - 3.2) * 4)
                total_score = (rate_score * 4) + (kw_hits * 3) + punctuation_bonus + (dur / 10)

                candidates.append({
                    "start": round(win_start, 2),
                    "end": round(end_seg["end"], 2),
                    "duration": round(dur, 2),
                    "score": total_score,
                    "text": window_text[:120],
                    "raw_text": " ".join([s["text"] for s in segments[i:j+1]])
                })

        # Sort by score descending
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Non-overlapping selection
        selected = []
        for cand in candidates:
            # Check overlap with already selected
            overlap = False
            for s in selected:
                # Overlap check
                if not (cand["end"] <= s["start"] or cand["start"] >= s["end"]):
                    overlap = True
                    break
            if not overlap:
                selected.append(cand)
            if len(selected) >= max_clips:
                break

        # If no clips selected (e.g. very quiet), create linear chunks
        if not selected:
            step = max_duration
            for t in range(0, int(total_duration), int(step)):
                end_t = min(total_duration, t + step)
                if end_t - t >= min_duration:
                    selected.append({
                        "start": float(t),
                        "end": float(end_t),
                        "score": 50,
                        "raw_text": ""
                    })
                if len(selected) >= max_clips:
                    break

        # Generate catchy hook titles and metadata
        hook_titles = [
            "O MOMENTO QUE CHOCOU A TODOS! ⚡",
            "VOCÊ NUNCA IMAGINOU ISSO! 🤯",
            "A VERDADE POR TRÁS DISSO! 🔥",
            "MOMENTO LENDÁRIO NA TRANSMISSÃO! 🚀",
            "ELE DISSE ISSO MESMO?! 😱"
        ]

        final_clips = []
        for idx, item in enumerate(selected):
            base_score = min(98, max(75, int(75 + (item.get("score", 10) / 3))))
            title = hook_titles[idx % len(hook_titles)]
            
            # Try deriving title from first sentence if punchy
            first_words = item.get("raw_text", "").split()[:6]
            if len(first_words) >= 3:
                cand_title = " ".join(first_words).upper() + "!"
                if len(cand_title) <= 45:
                    title = cand_title

            final_clips.append({
                "start": item["start"],
                "end": item["end"],
                "hook_title": title,
                "virality_score": base_score,
                "hook_score": min(99, base_score + 2),
                "retention_score": min(99, base_score - 1),
                "shareability_score": min(99, base_score + 1),
                "summary": f"Trecho com alta taxa de engajamento ({item['end'] - item['start']:.1f}s).",
                "hashtags": ["#viral", "#shorts", "#clipe", "#reels"]
            })

        return final_clips
