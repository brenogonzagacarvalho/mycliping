import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from app.config import CLIPS_DIR

logger = logging.getLogger("mycliping.storage")
INDEX_FILE = CLIPS_DIR / "index.json"

class ClipStorage:
    def __init__(self):
        self._ensure_index()

    def _ensure_index(self):
        if not INDEX_FILE.exists():
            with open(INDEX_FILE, "w", encoding="utf-8") as f:
                json.dump([], f)

    def list_clips(self) -> List[Dict[str, Any]]:
        self._ensure_index()
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                clips = json.load(f)
                return sorted(clips, key=lambda x: x.get("created_at", 0), reverse=True)
        except Exception as e:
            logger.error(f"Error reading clips index: {e}")
            return []

    def save_clip(self, clip_data: Dict[str, Any]):
        clips = self.list_clips()
        # Remove existing if same id
        clips = [c for c in clips if c.get("id") != clip_data.get("id")]
        clips.insert(0, clip_data)
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(clips, f, indent=2, ensure_ascii=False)

    def get_clip(self, clip_id: str) -> Optional[Dict[str, Any]]:
        clips = self.list_clips()
        for c in clips:
            if c.get("id") == clip_id:
                return c
        return None

    def delete_clip(self, clip_id: str) -> bool:
        clips = self.list_clips()
        target = self.get_clip(clip_id)
        if not target:
            return False

        # Delete clip folder or files if they exist
        clip_dir = CLIPS_DIR / clip_id
        if clip_dir.exists():
            import shutil
            shutil.rmtree(clip_dir, ignore_errors=True)

        new_clips = [c for c in clips if c.get("id") != clip_id]
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(new_clips, f, indent=2, ensure_ascii=False)
        return True

clip_storage = ClipStorage()
