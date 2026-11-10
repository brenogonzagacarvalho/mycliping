import os
import re
import math
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger("mycliping.caption_engine")

# Helper to format seconds to ASS timestamp (H:MM:SS.cs)
def to_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

STYLES_CONFIG = {
    "mrbeast": {
        "font": "Arial Black",
        "font_size": 68,
        "primary_color": "&H00FFFFFF",     # White
        "highlight_color": "&H0000FFFF",   # Bright Yellow (BGR format in ASS: 0x00FFFF = yellow)
        "outline_color": "&H00000000",     # Black
        "outline": 7,
        "shadow": 3,
        "bold": -1,
        "spacing": 1,
        "margin_v": 320                    # Distance from bottom
    },
    "hormozi": {
        "font": "Impact",
        "font_size": 74,
        "primary_color": "&H00FFFFFF",     # White
        "highlight_color": "&H0033FF33",   # Neon Green
        "outline_color": "&H00000000",     # Black
        "outline": 8,
        "shadow": 2,
        "bold": -1,
        "spacing": 2,
        "margin_v": 340
    },
    "neon": {
        "font": "Arial",
        "font_size": 66,
        "primary_color": "&H00FFFFFF",     # White
        "highlight_color": "&H00FFFF00",   # Cyan
        "outline_color": "&H007F00FF",     # Neon Purple outline
        "outline": 6,
        "shadow": 4,
        "bold": -1,
        "spacing": 1,
        "margin_v": 320
    },
    "minimal": {
        "font": "Arial",
        "font_size": 58,
        "primary_color": "&H00FFFFFF",     # White
        "highlight_color": "&H00E0E0E0",   # Soft white
        "outline_color": "&H90000000",     # Soft shadow
        "outline": 4,
        "shadow": 2,
        "bold": -1,
        "spacing": 0,
        "margin_v": 300
    },
    "fire": {
        "font": "Impact",
        "font_size": 72,
        "primary_color": "&H00FFFFFF",
        "highlight_color": "&H000077FF",   # Fire Orange
        "outline_color": "&H00000088",     # Dark Crimson outline
        "outline": 7,
        "shadow": 4,
        "bold": -1,
        "spacing": 1,
        "margin_v": 330
    }
}

class CaptionEngine:
    def __init__(self):
        pass

    def generate_ass(
        self,
        words: List[Dict[str, Any]],
        clip_start: float,
        clip_end: float,
        output_file: str,
        style_name: str = "mrbeast",
        hook_title: Optional[str] = None,
        video_w: int = 1080,
        video_h: int = 1920
    ) -> str:
        """
        Generates an Advanced SubStation Alpha (.ass) subtitle file
        with word-by-word karaoke highlight synced to speech.
        """
        style = STYLES_CONFIG.get(style_name.lower(), STYLES_CONFIG["mrbeast"])
        
        # Filter words within the clip range and adjust relative timestamps
        clip_words = []
        for w in words:
            # If word overlaps clip range
            if w["end"] > clip_start and w["start"] < clip_end:
                rel_start = max(0.0, w["start"] - clip_start)
                rel_end = max(rel_start + 0.05, min(clip_end - clip_start, w["end"] - clip_start))
                clip_words.append({
                    "word": w["word"].strip(),
                    "start": rel_start,
                    "end": rel_end
                })

        # Group words into short readable chunks (typically 3 to 5 words per subtitle event)
        chunks = self._group_words_into_chunks(clip_words, max_words=4)

        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CaptionStyle,{style['font']},{style['font_size']},{style['primary_color']},&H000000FF,{style['outline_color']},&H80000000,{style['bold']},0,0,0,100,100,{style['spacing']},0,1,{style['outline']},{style['shadow']},2,40,40,{style['margin_v']},1
Style: HookTitleStyle,Arial Black,54,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,6,3,8,40,40,160,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        events = []

        # 1. Add Hook Title Banner at top of video if provided
        if hook_title:
            clean_title = hook_title.upper().replace("\n", " ")
            clip_dur = max(1.0, clip_end - clip_start)
            # Display hook title for the first 5 seconds or entire clip if short
            title_dur = min(clip_dur, 6.0)
            events.append(
                f"Dialogue: 1,{to_ass_time(0.0)},{to_ass_time(title_dur)},HookTitleStyle,,0,0,0,,{clean_title}"
            )

        # 2. Add word-by-word karaoke lines
        for chunk in chunks:
            chunk_start = chunk[0]["start"]
            chunk_end = chunk[-1]["end"]

            # For each word in this chunk, create a dialogue line where that word is highlighted
            for i, active_word in enumerate(chunk):
                w_start = active_word["start"]
                w_end = active_word["end"]
                if w_end <= w_start:
                    w_end = w_start + 0.1

                # Construct styled line
                formatted_words = []
                for j, w in enumerate(chunk):
                    text = w["word"].upper()
                    if j == i:
                        # Highlight active word with scale and highlight color
                        formatted_words.append(f"{{\\c{style['highlight_color']}\\fscx108\\fscy108}}{text}{{\\c{style['primary_color']}\\fscx100\\fscy100}}")
                    else:
                        formatted_words.append(text)

                line_text = " ".join(formatted_words)
                events.append(
                    f"Dialogue: 0,{to_ass_time(w_start)},{to_ass_time(w_end)},CaptionStyle,,0,0,0,,{line_text}"
                )

        full_ass = header + "\n".join(events) + "\n"

        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(full_ass)

        return str(out_path.resolve())

    def _group_words_into_chunks(self, words: List[Dict[str, Any]], max_words: int = 4) -> List[List[Dict[str, Any]]]:
        chunks = []
        current = []
        for w in words:
            current.append(w)
            # Break chunk on sentence terminators or word limit
            word_str = w["word"]
            if len(current) >= max_words or any(word_str.endswith(p) for p in [".", "!", "?", ","]):
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)
        return chunks
