import os
import time
import json
import uuid
import math
import logging
import threading
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional
import requests
from app.config import DATA_DIR
from app.core.storage import clip_storage

logger = logging.getLogger("mycliping.social")

SOCIAL_CONFIG_FILE = DATA_DIR / "social_config.json"
POST_QUEUE_FILE = DATA_DIR / "post_queue.json"

DEFAULT_SOCIAL_CONFIG = {
    "youtube": {
        "client_id": "",
        "client_secret": "",
        "access_token": "",
        "refresh_token": "",
        "expires_at": 0,
        "channel_title": "",
        "default_privacy": "public",  # public, unlisted, private
    },
    "tiktok": {
        "client_key": "",
        "client_secret": "",
        "access_token": "",
        "refresh_token": "",
        "expires_at": 0,
        "open_id": "",
        "display_name": "",
        "default_privacy": "PUBLIC_TO_EVERYONE",  # PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS, SELF_ONLY
    },
    "webhook": {
        "enabled": False,
        "url": "",
        "secret": "",
    },
}


class SocialPublisher:
    def __init__(self):
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._ensure_files()

    def _ensure_files(self):
        if not SOCIAL_CONFIG_FILE.exists():
            self._write_json(SOCIAL_CONFIG_FILE, DEFAULT_SOCIAL_CONFIG)
        if not POST_QUEUE_FILE.exists():
            self._write_json(POST_QUEUE_FILE, [])

    @staticmethod
    def _read_json(path: Path, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    @staticmethod
    def _write_json(path: Path, data):
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    # ------------------------------------------------------------------
    # Config & Connection Status
    # ------------------------------------------------------------------
    def get_config(self) -> Dict[str, Any]:
        with self._lock:
            saved = self._read_json(SOCIAL_CONFIG_FILE, {})
            merged = {
                "youtube": {**DEFAULT_SOCIAL_CONFIG["youtube"], **saved.get("youtube", {})},
                "tiktok": {**DEFAULT_SOCIAL_CONFIG["tiktok"], **saved.get("tiktok", {})},
                "webhook": {**DEFAULT_SOCIAL_CONFIG["webhook"], **saved.get("webhook", {})},
            }
            return merged

    def get_public_status(self) -> Dict[str, Any]:
        cfg = self.get_config()
        yt, tt, wh = cfg["youtube"], cfg["tiktok"], cfg["webhook"]
        return {
            "youtube": {
                "connected": bool(yt.get("refresh_token") or yt.get("access_token")),
                "channel_title": yt.get("channel_title", ""),
                "client_id": yt.get("client_id", ""),
                "has_secret": bool(yt.get("client_secret")),
                "default_privacy": yt.get("default_privacy", "public"),
            },
            "tiktok": {
                "connected": bool(tt.get("access_token") or tt.get("refresh_token")),
                "display_name": tt.get("display_name", ""),
                "client_key": tt.get("client_key", ""),
                "has_secret": bool(tt.get("client_secret")),
                "default_privacy": tt.get("default_privacy", "PUBLIC_TO_EVERYONE"),
            },
            "webhook": {
                "enabled": bool(wh.get("enabled")),
                "url": wh.get("url", ""),
                "has_secret": bool(wh.get("secret")),
            },
        }

    def update_credentials(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            cfg = self.get_config()
            for section in ("youtube", "tiktok", "webhook"):
                if section in payload and isinstance(payload[section], dict):
                    for k, v in payload[section].items():
                        # Don't overwrite secrets with empty placeholder if not sent
                        if k in ("client_secret", "secret") and v == "":
                            continue
                        cfg[section][k] = v
            self._write_json(SOCIAL_CONFIG_FILE, cfg)
        return self.get_public_status()

    def disconnect(self, platform: str):
        with self._lock:
            cfg = self.get_config()
            if platform == "youtube":
                cfg["youtube"].update({"access_token": "", "refresh_token": "", "expires_at": 0, "channel_title": ""})
            elif platform == "tiktok":
                cfg["tiktok"].update({"access_token": "", "refresh_token": "", "expires_at": 0, "open_id": "", "display_name": ""})
            self._write_json(SOCIAL_CONFIG_FILE, cfg)

    # ------------------------------------------------------------------
    # YouTube OAuth2 + Shorts Upload
    # ------------------------------------------------------------------
    def youtube_auth_url(self, redirect_uri: str) -> str:
        yt = self.get_config()["youtube"]
        if not yt.get("client_id"):
            raise RuntimeError("Configure o Client ID e Client Secret do Google/YouTube primeiro.")
        params = {
            "client_id": yt["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
            "access_type": "offline",
            "prompt": "consent",
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

    def youtube_handle_callback(self, code: str, redirect_uri: str) -> str:
        cfg = self.get_config()
        yt = cfg["youtube"]
        res = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": yt["client_id"],
                "client_secret": yt["client_secret"],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        res.raise_for_status()
        tok = res.json()
        yt["access_token"] = tok["access_token"]
        if tok.get("refresh_token"):
            yt["refresh_token"] = tok["refresh_token"]
        yt["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60

        # Fetch channel title
        try:
            ch = requests.get(
                "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                headers={"Authorization": f"Bearer {yt['access_token']}"},
                timeout=15,
            )
            if ch.ok and ch.json().get("items"):
                yt["channel_title"] = ch.json()["items"][0]["snippet"]["title"]
        except Exception:
            pass

        with self._lock:
            self._write_json(SOCIAL_CONFIG_FILE, cfg)
        return yt.get("channel_title") or "YouTube Conectado"

    def _youtube_token(self) -> str:
        cfg = self.get_config()
        yt = cfg["youtube"]
        if not yt.get("refresh_token") and not yt.get("access_token"):
            raise RuntimeError("Conta do YouTube não conectada.")
        if yt.get("access_token") and time.time() < yt.get("expires_at", 0):
            return yt["access_token"]
        if not yt.get("refresh_token"):
            raise RuntimeError("Token do YouTube expirado. Reconecte a conta.")

        res = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": yt["client_id"],
                "client_secret": yt["client_secret"],
                "refresh_token": yt["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        res.raise_for_status()
        tok = res.json()
        yt["access_token"] = tok["access_token"]
        yt["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        with self._lock:
            self._write_json(SOCIAL_CONFIG_FILE, cfg)
        return yt["access_token"]

    def publish_youtube_short(
        self, video_path: str, title: str, description: str, tags: List[str], privacy: str = "public"
    ) -> Dict[str, Any]:
        token = self._youtube_token()
        if "#shorts" not in title.lower():
            title = f"{title[:88]} #Shorts".strip()
        if "#shorts" not in description.lower():
            description = f"{description}\n\n#Shorts".strip()

        metadata = {
            "snippet": {
                "title": title[:100],
                "description": description[:4900],
                "tags": [t.lstrip("#") for t in tags][:15],
                "categoryId": "22",  # People & Blogs
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        file_size = os.path.getsize(video_path)
        init_res = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(file_size),
                "X-Upload-Content-Type": "video/mp4",
            },
            json=metadata,
            timeout=30,
        )
        init_res.raise_for_status()
        upload_url = init_res.headers["Location"]

        with open(video_path, "rb") as f:
            up_res = requests.put(
                upload_url,
                headers={"Content-Length": str(file_size), "Content-Type": "video/mp4"},
                data=f,
                timeout=300,
            )
        up_res.raise_for_status()
        video_id = up_res.json().get("id", "")
        return {"post_id": video_id, "url": f"https://youtube.com/shorts/{video_id}"}

    # ------------------------------------------------------------------
    # TikTok OAuth2 + Content Posting API v2
    # ------------------------------------------------------------------
    def tiktok_auth_url(self, redirect_uri: str) -> str:
        tt = self.get_config()["tiktok"]
        if not tt.get("client_key"):
            raise RuntimeError("Configure o Client Key e Client Secret do TikTok primeiro.")
        params = {
            "client_key": tt["client_key"],
            "response_type": "code",
            "scope": "user.info.basic,video.upload,video.publish",
            "redirect_uri": redirect_uri,
            "state": uuid.uuid4().hex[:12],
        }
        return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(params)

    def tiktok_handle_callback(self, code: str, redirect_uri: str) -> str:
        cfg = self.get_config()
        tt = cfg["tiktok"]
        res = requests.post(
            "https://open.api.tiktok.com/v2/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": tt["client_key"],
                "client_secret": tt["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        res.raise_for_status()
        tok = res.json()
        if "access_token" not in tok:
            raise RuntimeError(f"TikTok OAuth falhou: {tok}")
        tt["access_token"] = tok["access_token"]
        if tok.get("refresh_token"):
            tt["refresh_token"] = tok["refresh_token"]
        tt["open_id"] = tok.get("open_id", "")
        tt["expires_at"] = time.time() + int(tok.get("expires_in", 86400)) - 60

        try:
            u = requests.get(
                "https://open.api.tiktok.com/v2/user/info/?fields=display_name",
                headers={"Authorization": f"Bearer {tt['access_token']}"},
                timeout=15,
            )
            if u.ok:
                tt["display_name"] = u.json().get("data", {}).get("user", {}).get("display_name", "TikTok Conectado")
        except Exception:
            pass

        with self._lock:
            self._write_json(SOCIAL_CONFIG_FILE, cfg)
        return tt.get("display_name") or "TikTok Conectado"

    def _tiktok_token(self) -> str:
        cfg = self.get_config()
        tt = cfg["tiktok"]
        if not tt.get("access_token") and not tt.get("refresh_token"):
            raise RuntimeError("Conta do TikTok não conectada.")
        if tt.get("access_token") and time.time() < tt.get("expires_at", 0):
            return tt["access_token"]
        if not tt.get("refresh_token"):
            raise RuntimeError("Token do TikTok expirado. Reconecte a conta.")

        res = requests.post(
            "https://open.api.tiktok.com/v2/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": tt["client_key"],
                "client_secret": tt["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": tt["refresh_token"],
            },
            timeout=30,
        )
        res.raise_for_status()
        tok = res.json()
        tt["access_token"] = tok["access_token"]
        if tok.get("refresh_token"):
            tt["refresh_token"] = tok["refresh_token"]
        tt["expires_at"] = time.time() + int(tok.get("expires_in", 86400)) - 60
        with self._lock:
            self._write_json(SOCIAL_CONFIG_FILE, cfg)
        return tt["access_token"]

    def publish_tiktok_video(
        self, video_path: str, title: str, privacy_level: str = "PUBLIC_TO_EVERYONE"
    ) -> Dict[str, Any]:
        token = self._tiktok_token()
        file_size = os.path.getsize(video_path)
        chunk_size = min(file_size, 10 * 1024 * 1024)
        total_chunks = math.ceil(file_size / chunk_size)

        init_res = requests.post(
            "https://open.api.tiktok.com/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={
                "post_info": {
                    "title": title[:150],
                    "privacy_level": privacy_level,
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": total_chunks,
                },
            },
            timeout=30,
        )
        init_res.raise_for_status()
        data = init_res.json().get("data", {})
        upload_url = data.get("upload_url")
        publish_id = data.get("publish_id", "")
        if not upload_url:
            raise RuntimeError(f"TikTok init erro: {init_res.text}")

        with open(video_path, "rb") as f:
            for idx in range(total_chunks):
                chunk = f.read(chunk_size)
                start_byte = idx * chunk_size
                end_byte = start_byte + len(chunk) - 1
                up = requests.put(
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                    },
                    data=chunk,
                    timeout=180,
                )
                up.raise_for_status()

        return {"post_id": publish_id, "url": "https://www.tiktok.com"}

    # ------------------------------------------------------------------
    # Optional Webhook (n8n / Make / Zapier)
    # ------------------------------------------------------------------
    def publish_webhook(
        self, video_path: str, platform: str, title: str, description: str, tags: List[str]
    ) -> Dict[str, Any]:
        wh = self.get_config()["webhook"]
        if not wh.get("enabled") or not wh.get("url"):
            raise RuntimeError("Webhook não configurado.")
        headers = {"X-ClipStudio-Secret": wh["secret"]} if wh.get("secret") else {}
        with open(video_path, "rb") as f:
            res = requests.post(
                wh["url"],
                headers=headers,
                data={
                    "platform": platform,
                    "title": title,
                    "description": description,
                    "hashtags": " ".join(tags),
                },
                files={"video": (os.path.basename(video_path), f, "video/mp4")},
                timeout=180,
            )
        res.raise_for_status()
        return {"post_id": f"webhook_{int(time.time())}", "url": wh["url"]}

    # ------------------------------------------------------------------
    # Queue & Scheduler
    # ------------------------------------------------------------------
    def list_queue(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = self._read_json(POST_QUEUE_FILE, [])
        return sorted(items, key=lambda x: x.get("scheduled_at", 0))

    def enqueue_posts(
        self,
        clip_ids: List[str],
        platforms: List[str],
        start_timestamp: Optional[float] = None,
        interval_hours: float = 0.0,
        custom_titles: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        base_time = start_timestamp if start_timestamp and start_timestamp > time.time() else time.time()
        created: List[Dict[str, Any]] = []
        custom_titles = custom_titles or {}

        with self._lock:
            queue = self._read_json(POST_QUEUE_FILE, [])
            for idx, cid in enumerate(clip_ids):
                clip = clip_storage.get_clip(cid)
                if not clip:
                    continue
                tags = clip.get("hashtags") or ["#viral", "#shorts"]
                title = custom_titles.get(cid) or clip.get("hook_title") or "Clipe Viral"
                desc = f"{clip.get('summary', '')}\n\n{' '.join(tags)}".strip()
                sched_t = base_time + idx * interval_hours * 3600

                for plat in platforms:
                    item = {
                        "id": f"post_{uuid.uuid4().hex[:8]}",
                        "clip_id": cid,
                        "clip_title": title,
                        "description": desc,
                        "hashtags": tags,
                        "platform": plat,
                        "status": "scheduled",   # scheduled, uploading, published, error
                        "scheduled_at": sched_t,
                        "created_at": time.time(),
                        "published_url": "",
                        "error": "",
                    }
                    queue.append(item)
                    created.append(item)

            self._write_json(POST_QUEUE_FILE, queue)
        return created

    def delete_queue_item(self, post_id: str) -> bool:
        with self._lock:
            queue = self._read_json(POST_QUEUE_FILE, [])
            new_q = [p for p in queue if p.get("id") != post_id]
            if len(new_q) == len(queue):
                return False
            self._write_json(POST_QUEUE_FILE, new_q)
            return True

    def _update_item(self, post_id: str, updates: Dict[str, Any]):
        with self._lock:
            queue = self._read_json(POST_QUEUE_FILE, [])
            for p in queue:
                if p.get("id") == post_id:
                    p.update(updates)
                    break
            self._write_json(POST_QUEUE_FILE, queue)

    def process_item(self, item: Dict[str, Any]):
        post_id = item["id"]
        self._update_item(post_id, {"status": "uploading", "error": ""})
        try:
            clip = clip_storage.get_clip(item["clip_id"])
            if not clip or not os.path.exists(clip["video_path"]):
                raise RuntimeError("Arquivo de vídeo do clipe não encontrado.")

            cfg = self.get_config()
            plat = item["platform"]
            video_path = clip["video_path"]
            title = item["clip_title"]
            desc = item.get("description", "")
            tags = item.get("hashtags", [])

            if plat == "youtube_shorts":
                if cfg["youtube"].get("refresh_token") or cfg["youtube"].get("access_token"):
                    res = self.publish_youtube_short(
                        video_path, title, desc, tags, cfg["youtube"].get("default_privacy", "public")
                    )
                elif cfg["webhook"].get("enabled"):
                    res = self.publish_webhook(video_path, plat, title, desc, tags)
                else:
                    raise RuntimeError("Conecte o YouTube (ou ative o Webhook) na aba Auto-Postagem.")
            elif plat == "tiktok":
                if cfg["tiktok"].get("access_token") or cfg["tiktok"].get("refresh_token"):
                    res = self.publish_tiktok_video(
                        video_path, f"{title} {' '.join(tags)}".strip(),
                        cfg["tiktok"].get("default_privacy", "PUBLIC_TO_EVERYONE")
                    )
                elif cfg["webhook"].get("enabled"):
                    res = self.publish_webhook(video_path, plat, title, desc, tags)
                else:
                    raise RuntimeError("Conecte o TikTok (ou ative o Webhook) na aba Auto-Postagem.")
            else:
                res = self.publish_webhook(video_path, plat, title, desc, tags)

            self._update_item(post_id, {
                "status": "published",
                "published_url": res.get("url", ""),
                "published_at": time.time(),
            })
            logger.info(f"Published post {post_id} to {plat}: {res.get('url')}")
        except Exception as e:
            logger.error(f"Failed to publish {post_id}: {e}")
            self._update_item(post_id, {"status": "error", "error": str(e)})

    def start_scheduler(self):
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return
        self._stop_event.clear()

        def loop():
            while not self._stop_event.wait(15):
                now = time.time()
                due = [p for p in self.list_queue() if p["status"] == "scheduled" and p["scheduled_at"] <= now]
                for item in due:
                    if self._stop_event.is_set():
                        break
                    self.process_item(item)

        self._scheduler_thread = threading.Thread(target=loop, daemon=True, name="social-scheduler")
        self._scheduler_thread.start()

    def stop_scheduler(self):
        self._stop_event.set()


social_publisher = SocialPublisher()
