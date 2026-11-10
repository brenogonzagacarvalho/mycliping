import os
import sys
import json
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mycliping")

# Root directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
CLIPS_DIR = DATA_DIR / "clips"
CACHE_DIR = DATA_DIR / "cache"
LIVE_BUFFER_DIR = DATA_DIR / "live_buffer"
SETTINGS_FILE = DATA_DIR / "settings.json"

for folder in [DATA_DIR, UPLOADS_DIR, CLIPS_DIR, CACHE_DIR, LIVE_BUFFER_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# Locate and configure FFmpeg binary
FFMPEG_PATH = "ffmpeg"
try:
    import imageio_ffmpeg
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    bin_dir = os.path.dirname(ffmpeg_bin)
    ffmpeg_standard = os.path.join(bin_dir, "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if not os.path.exists(ffmpeg_standard) and os.path.exists(ffmpeg_bin):
        try:
            import shutil
            shutil.copyfile(ffmpeg_bin, ffmpeg_standard)
        except Exception:
            pass
    FFMPEG_PATH = ffmpeg_standard if os.path.exists(ffmpeg_standard) else ffmpeg_bin
    
    # Prepend to PATH so yt-dlp and other tools pick it up automatically
    if bin_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    logger.info(f"Using FFmpeg binary at: {FFMPEG_PATH}")
except Exception as e:
    logger.warning(f"Could not configure imageio-ffmpeg: {e}. Falling back to system 'ffmpeg'")

DEFAULT_SETTINGS = {
    "whisper_model": "base",       # tiny, base, small, medium
    "whisper_language": "auto",    # auto, pt, en, es
    "ai_provider": "heuristic",    # heuristic, ollama, gemini, openai, groq
    "ollama_url": "http://localhost:11434",
    "ollama_model": "llama3.2:latest",
    "gemini_api_key": "",
    "gemini_model": "gemini-1.5-flash",
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "groq_api_key": "",
    "groq_model": "llama-3.3-70b-versatile",
    "caption_style": "mrbeast",    # mrbeast, hormozi, neon, minimal, fire
    "aspect_ratio": "9:16",        # 9:16, 1:1, 16:9
    "layout_mode": "smart_face",   # smart_face, split_screen, blurred_bg, original
    "max_clips": 4,
    "min_clip_duration": 25,
    "max_clip_duration": 60,
    "font_size": 70,
    "burn_hook_title": True,
    "live_buffer_seconds": 300     # 5 minutes rolling live buffer
}

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                merged = {**DEFAULT_SETTINGS, **saved}
                return merged
        except Exception as e:
            logger.error(f"Error loading settings: {e}")
    return DEFAULT_SETTINGS.copy()

def save_settings(new_settings: dict) -> dict:
    current = load_settings()
    current.update(new_settings)
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving settings: {e}")
    return current
