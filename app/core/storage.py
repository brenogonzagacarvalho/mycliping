import os
import json
import logging
import shutil
import threading
from typing import List, Dict, Any, Optional
from app.config import CLIPS_DIR

logger = logging.getLogger("mycliping.storage")
INDEX_FILE = CLIPS_DIR / "index.json"


class ClipStorage:
    """Thread-safe JSON index of rendered clips (several jobs may save at once)."""

    def __init__(self):
        self._lock = threading.RLock()
        self._ensure_index()

    def _ensure_index(self):
        with self._lock:
            if not INDEX_FILE.exists():
                self._write([])

    def _read(self) -> List[Dict[str, Any]]:
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.error(f"Error reading clips index: {e}")
            return []

    def _write(self, clips: List[Dict[str, Any]]):
        tmp = INDEX_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clips, f, indent=2, ensure_ascii=False)
        os.replace(tmp, INDEX_FILE)

    def list_clips(self) -> List[Dict[str, Any]]:
        with self._lock:
            clips = self._read()
        return sorted(clips, key=lambda x: x.get("created_at", 0), reverse=True)

    def save_clip(self, clip_data: Dict[str, Any]):
        with self._lock:
            clips = [c for c in self._read() if c.get("id") != clip_data.get("id")]
            clips.insert(0, clip_data)
            self._write(clips)

    def update_clip(self, clip_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            clips = self._read()
            updated = None
            for c in clips:
                if c.get("id") == clip_id:
                    c.update(updates)
                    updated = c
                    break
            if updated is not None:
                self._write(clips)
            return updated

    def get_clip(self, clip_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for c in self._read():
                if c.get("id") == clip_id:
                    return c
        return None

    def delete_clip(self, clip_id: str) -> bool:
        with self._lock:
            clips = self._read()
            if not any(c.get("id") == clip_id for c in clips):
                return False
            self._write([c for c in clips if c.get("id") != clip_id])

        clip_dir = CLIPS_DIR / clip_id
        if clip_dir.exists():
            shutil.rmtree(clip_dir, ignore_errors=True)
        return True


clip_storage = ClipStorage()
