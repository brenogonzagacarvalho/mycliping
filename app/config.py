import os
import sys
import json
import logging
import threading
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

# Hidden console window for subprocesses on Windows
SUBPROCESS_FLAGS = 0
if sys.platform == "win32":
    import subprocess as _sp
    SUBPROCESS_FLAGS = _sp.CREATE_NO_WINDOW


# ---------------------------------------------------------------------------
# Hardware detection (used to pick safe concurrency defaults)
# ---------------------------------------------------------------------------
def _total_ram_gb() -> float:
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return round(stat.ullTotalPhys / (1024 ** 3), 1)
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return round(pages * page_size / (1024 ** 3), 1)
    except Exception:
        return 8.0


CPU_THREADS = os.cpu_count() or 2
RAM_GB = _total_ram_gb()


def _cpu_name() -> str:
    try:
        if sys.platform == "win32":
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        import platform
        return platform.processor() or "CPU"
    except Exception:
        return "CPU"


CPU_NAME = _cpu_name()


def recommended_concurrent_jobs() -> int:
    """
    Each job runs Whisper (uses every CPU thread) + FFmpeg. Running more jobs
    than the CPU can feed only makes all of them slower, so we scale with
    ~6 threads and ~6 GB RAM per job.
    """
    by_cpu = max(1, CPU_THREADS // 6)
    by_ram = max(1, int(RAM_GB // 6))
    return max(1, min(by_cpu, by_ram, 4))


DEFAULT_SETTINGS = {
    "whisper_model": "base",       # tiny, base, small, medium
    "whisper_language": "auto",    # auto, pt, en, es
    "ai_provider": "heuristic",    # heuristic, ollama, gemini, openai, groq
    "ollama_url": "http://localhost:11434",
    "ollama_model": "llama3.2:latest",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",
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
    "live_buffer_seconds": 300,    # 5 minutes rolling live buffer
    # Performance
    "max_concurrent_jobs": recommended_concurrent_jobs(),
    "video_encoder": "auto",       # auto, libx264, h264_qsv, h264_nvenc, h264_amf
    "render_workers": 0,           # 0 = automatic
    "cleanup_source_after": True,  # delete downloaded source/audio after clips are rendered
}

_DEPRECATED_GEMINI_PREFIXES = ("gemini-1.0", "gemini-1.5", "gemini-pro")
_settings_lock = threading.Lock()


def load_settings() -> dict:
    with _settings_lock:
        merged = DEFAULT_SETTINGS.copy()
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                merged.update(saved)
            except Exception as e:
                logger.error(f"Error loading settings: {e}")

        # Migrate retired Gemini models
        if str(merged.get("gemini_model", "")).startswith(_DEPRECATED_GEMINI_PREFIXES):
            merged["gemini_model"] = DEFAULT_SETTINGS["gemini_model"]
        return merged


def save_settings(new_settings: dict) -> dict:
    current = load_settings()
    current.update(new_settings or {})
    try:
        current["max_concurrent_jobs"] = max(1, min(8, int(current.get("max_concurrent_jobs") or 1)))
    except (TypeError, ValueError):
        current["max_concurrent_jobs"] = recommended_concurrent_jobs()
    with _settings_lock:
        try:
            tmp = SETTINGS_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2, ensure_ascii=False)
            os.replace(tmp, SETTINGS_FILE)
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
    return current
