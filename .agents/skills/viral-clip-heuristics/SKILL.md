---
name: viral-clip-heuristics
description: Use this skill when modifying AI prompts (Gemini, Ollama, OpenAI, Groq), audio energy heuristics, hook detection algorithms, or clip selection criteria in MyCliping.
---

# Viral Clip Heuristics & AI Guidelines

This skill defines the criteria, JSON contract, and heuristic fallbacks for finding high-performing vertical clips in [`ai_analyzer.py`](file:///c:/Users/Usuario/Documents/Dev/mycliping/app/core/ai_analyzer.py).

---

## 1. Viral Clip Selection Criteria

Every generated clip must strictly adhere to the 4 Viral Pillars:

1. **3-Second Hook**: The start timestamp must coincide with an intriguing question, strong declaration, reaction, or dramatic sentence.
2. **Complete Narrative Arc**: Never cut mid-sentence. Always expand the start/end window to nearest sentence boundaries in transcription tokens.
3. **Pacing / Duration**:
   * Minimum: 25 seconds (allows context building).
   * Maximum: 60 seconds (optimized for Shorts/TikTok retention).
4. **Hook Title**: Max 6 words, all uppercase, formatted with high emotional curiosity and 1-2 relevant emojis (e.g., `"ELE FEZ ISSO AO VIVO?! 😱"`).

---

## 2. JSON Response Contract

When querying LLM providers (Ollama, Gemini, Groq, OpenAI), always validate against this schema:

```json
[
  {
    "start": 14.5,
    "end": 52.0,
    "hook_title": "ELE FEZ ISSO AO VIVO?! 😱",
    "virality_score": 96,
    "hook_score": 98,
    "retention_score": 94,
    "shareability_score": 95,
    "summary": "Momento de alta tensão onde acontece a revelação principal.",
    "hashtags": ["#viral", "#foryou", "#momentos", "#shorts"]
  }
]
```

---

## 3. Heuristic Fallback (Offline Mode)

When no LLM API key or Ollama instance is configured:
1. **RMS Audio Energy**: Scan audio wave for amplitude peaks (laughter, screams, sudden volume increases).
2. **Speech Rate / Cadence**: High word density (words per second > 3.0) signals climactic or passionate segments.
3. **Keyword Triggers**: Prioritize segments containing exclamation points, question words ("Por que", "Como", "Olha isso", "Nunca"), and sentiment swings.

