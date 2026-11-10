import os
import cv2
import numpy as np
import logging
from typing import List, Tuple, Dict, Any, Optional
from pathlib import Path
from app.config import DATA_DIR

logger = logging.getLogger("mycliping.face_tracker")

CASCADE_PATH = DATA_DIR / "models" / "haarcascade_frontalface_default.xml"

class FaceTracker:
    def __init__(self):
        self.detector = None
        self.available = False
        self._init_detector()

    def _init_detector(self):
        try:
            if not CASCADE_PATH.exists():
                CASCADE_PATH.parent.mkdir(parents=True, exist_ok=True)
                import urllib.request
                import ssl
                ctx = ssl._create_unverified_context()
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

    def analyze_face_positions(
        self,
        video_path: str,
        start_time: float,
        end_time: float,
        sample_fps: float = 2.0
    ) -> List[Tuple[float, float, float, float]]:
        """
        Samples video frames between start_time and end_time.
        Returns a list of (timestamp, center_x, center_y, face_width) normalized (0.0 to 1.0).
        """
        if not self.available or self.detector is None:
            return [(0.0, 0.5, 0.5, 0.2)]

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error(f"Cannot open video for face tracking: {video_path}")
            return [(0.0, 0.5, 0.5, 0.2)]

        video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_step = max(1, int(video_fps / sample_fps))
        start_frame = int(start_time * video_fps)
        end_frame = int(end_time * video_fps)

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        current_frame = start_frame
        detections: List[Tuple[float, float, float, float]] = []

        last_known_x = 0.5
        last_known_y = 0.4
        last_known_w = 0.2

        while current_frame <= end_frame:
            ret, frame = cap.read()
            if not ret:
                break

            if (current_frame - start_frame) % frame_step == 0:
                t = (current_frame - start_frame) / video_fps
                h, w = frame.shape[:2]
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self.detector.detectMultiScale(
                    gray,
                    scaleFactor=1.15,
                    minNeighbors=4,
                    minSize=(int(w * 0.08), int(h * 0.08))
                )

                best_x, best_y, best_w = last_known_x, last_known_y, last_known_w
                if len(faces) > 0:
                    # Pick largest face (primary speaker)
                    largest = max(faces, key=lambda f: f[2] * f[3])
                    fx, fy, fw, fh = largest
                    best_x = (fx + fw / 2.0) / w
                    best_y = (fy + fh / 2.0) / h
                    best_w = fw / w

                    last_known_x = best_x
                    last_known_y = best_y
                    last_known_w = best_w

                detections.append((t, best_x, best_y, best_w))

            current_frame += 1

        cap.release()

        if not detections:
            return [(0.0, 0.5, 0.5, 0.2)]

        return detections

    def compute_smoothed_crop_x(
        self,
        detections: List[Tuple[float, float, float, float]],
        src_w: int,
        src_h: int,
        target_w: int = 1080,
        target_h: int = 1920
    ) -> float:
        """
        Computes a stable horizontal center for 9:16 crop.
        Uses median/exponential smoothing to avoid jittery movements.
        """
        if not detections:
            return src_w / 2.0

        crop_w_src = int(src_h * (9 / 16))
        if crop_w_src > src_w:
            crop_w_src = src_w

        xs = [d[1] for d in detections]
        med_x = float(np.median(xs))
        target_center_x = med_x * src_w

        half_crop = crop_w_src / 2.0
        clamped_center_x = max(half_crop, min(src_w - half_crop, target_center_x))

        return clamped_center_x
