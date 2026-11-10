import sys
import subprocess
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def check_all():
    print("=== MyCliping Diagnostic Check ===")
    
    # 1. Check Python
    print(f"[1] Python version: {sys.version.split()[0]}")
    
    # 2. Check FFmpeg
    try:
        from app.config import FFMPEG_PATH
        res = subprocess.run([FFMPEG_PATH, "-version"], capture_output=True, text=True, timeout=5)
        first_line = res.stdout.splitlines()[0] if res.stdout else "unknown"
        print(f"[2] FFmpeg: OK ({first_line})")
    except Exception as e:
        print(f"[2] FFmpeg: FAILED ({e})")
        
    # 3. Check OpenCV
    try:
        import cv2
        print(f"[3] OpenCV: OK ({cv2.__version__})")
    except Exception as e:
        print(f"[3] OpenCV: FAILED ({e})")
        
    # 4. Check Faster Whisper / CTranslate2
    try:
        import ctranslate2
        import faster_whisper
        cuda_ok = False
        try:
            cuda_ok = "cuda" in ctranslate2.get_supported_compute_types("cuda")
        except Exception:
            cuda_ok = False
        print(f"[4] Faster-Whisper: OK (faster-whisper {faster_whisper.__version__}, Device: {'CUDA' if cuda_ok else 'CPU'})")
    except Exception as e:
        print(f"[4] Faster-Whisper: FAILED ({e})")
        
    # 5. Check MediaPipe
    try:
        import mediapipe as mp
        print(f"[5] MediaPipe: OK ({mp.__version__})")
    except Exception as e:
        print(f"[5] MediaPipe: FAILED ({e})")

    print("==================================")

if __name__ == "__main__":
    check_all()
