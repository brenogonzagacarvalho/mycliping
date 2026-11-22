import subprocess
import logging
from typing import List, Tuple, Optional
import cv2
import numpy as np
from app.config import DATA_DIR, FFMPEG_PATH, SUBPROCESS_FLAGS

logger = logging.getLogger("mycliping.face_tracker")

CASCADE_PATH = DATA_DIR / "models" / "haarcascade_frontalface_default.xml"

# Detections are (timestamp, center_x, center_y, face_width), all normalized 0..1
Detection = Tuple[float, float, float, float]


class FaceTracker:
    ANALYSIS_WIDTH = 480  # faces are detected on a small grayscale copy (much faster)

    def __init__(self):
        self.detector = None
        self.available = False
        self._init_detector()

    def _init_detector(self):
        try:
            if not CASCADE_PATH.exists():
                CASCADE_PATH.parent.mkdir(parents=True, exist_ok=True)
                import urllib.request
                url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
                urllib.request.urlretrieve(url, str(CASCADE_PATH.resolve()))

            if CASCADE_PATH.exists():
                clf = cv2.CascadeClassifier(str(CASCADE_PATH.resolve()))
                if not clf.empty():
                    self.detector = clf
                    self.available = True
                    logger.info("OpenCV Face Detection initialized successfully.")
                    return
        except Exception as e:
            logger.warning(f"Face detector init error: {e}. Falling back to center crop.")

        logger.info("Using smart center focus for 9:16 vertical crop.")
        self.available = False

    def _read_gray_frames(
        self, video_path: str, start: float, duration: float, w: int, h: int, keyframes_only: bool
    ) -> List[np.ndarray]:
        """Decodes small grayscale frames with FFmpeg (multithreaded, can decode keyframes only)."""
        cmd = [FFMPEG_PATH, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if keyframes_only:
            cmd += ["-skip_frame", "nokey"]
        cmd += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", video_path, "-an", "-sn"]
        if keyframes_only:
            cmd += ["-vf", f"scale={w}:{h}", "-fps_mode", "passthrough"]
        else:
            cmd += ["-vf", f"fps=1,scale={w}:{h}"]
        cmd += ["-f", "rawvideo", "-pix_fmt", "gray", "-"]

        res = subprocess.run(cmd, capture_output=True, timeout=180, creationflags=SUBPROCESS_FLAGS)
        frame_size = w * h
        data = res.stdout
        n = len(data) // frame_size
        return [np.frombuffer(data, dtype=np.uint8, count=frame_size, offset=i * frame_size).reshape(h, w) for i in range(n)]

    def analyze_face_positions(
        self,
        video_path: str,
        start_time: float,
        end_time: float,
        src_w: Optional[int] = None,
        src_h: Optional[int] = None,
    ) -> List[Detection]:
        """
        Samples frames between start_time and end_time and returns only frames
        where a face was found. Empty list = no face (caller centers the crop).
        """
        if not self.available or self.detector is None:
            return []

        if not src_w or not src_h:
            cap = cv2.VideoCapture(video_path)
            src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
            src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
            cap.release()

        w = self.ANALYSIS_WIDTH
        h = max(2, int(round(w * src_h / src_w / 2)) * 2)
        duration = max(0.5, end_time - start_time)

        try:
            frames = self._read_gray_frames(video_path, start_time, duration, w, h, keyframes_only=True)
            if len(frames) < 4:  # sparse keyframes: decode 1 frame per second instead
                frames = self._read_gray_frames(video_path, start_time, duration, w, h, keyframes_only=False)
        except Exception as e:
            logger.warning(f"Face tracking frame extraction failed: {e}")
            return []

        detections: List[Detection] = []
        step_t = duration / max(1, len(frames))
        for i, gray in enumerate(frames):
            gray = cv2.equalizeHist(gray)
            faces = self.detector.detectMultiScale(
                gray,
                scaleFactor=1.12,
                minNeighbors=5,
                minSize=(max(16, int(w * 0.05)), max(16, int(h * 0.05)))
            )
            if len(faces) > 0:
                fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])  # largest face = main speaker
                detections.append((i * step_t, (fx + fw / 2.0) / w, (fy + fh / 2.0) / h, fw / w))

        logger.info(f"Face tracking: {len(detections)}/{len(frames)} sampled frames with faces.")
        return detections

    @staticmethod
    def face_summary(detections: List[Detection]) -> Optional[Tuple[float, float, float]]:
        """Median (x, y, width) of the detected faces, normalized."""
        if not detections:
            return None
        arr = np.array([(d[1], d[2], d[3]) for d in detections])
        med = np.median(arr, axis=0)
        return float(med[0]), float(med[1]), float(med[2])

    def compute_smoothed_crop_x(self, detections: List[Detection], src_w: int, crop_w: int) -> float:
        """Stable horizontal crop center (median of faces, clamped inside the frame)."""
        summary = self.face_summary(detections)
        target_center_x = (summary[0] * src_w) if summary else src_w / 2.0
        half_crop = crop_w / 2.0
        return max(half_crop, min(src_w - half_crop, target_center_x))
