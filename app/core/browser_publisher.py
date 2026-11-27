import os
import time
import logging
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.config import DATA_DIR

logger = logging.getLogger("mycliping.browser_pub")

BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"
BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def _find_channel() -> str:
    if os.path.exists(r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        return "chrome"
    if os.path.exists(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        return "msedge"
    return "chrome"


class BrowserPublisher:
    """
    Single-user persistent browser publisher (Playwright + Chrome/Edge).
    Lets the user log in once normally on YouTube Studio / TikTok Studio
    without needing Developer Apps, OAuth review, or Domain Verification.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._login_proc_active = False

    def is_profile_initialized(self) -> bool:
        return (BROWSER_PROFILE_DIR / "Default").exists()

    def _launch_context(self, p, headless: bool = False):
        channel = _find_channel()
        return p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR.resolve()),
            channel=channel,
            headless=headless,
            viewport={"width": 1280, "height": 850},
            args=["--disable-blink-features=AutomationControlled"],
        )

    def open_login_session(self, target: str = "all"):
        """Opens a visible browser window to YouTube Studio and/or TikTok Studio for 1-time login."""
        if self._login_proc_active:
            raise RuntimeError("Uma janela de login já está aberta. Faça o login e feche-a primeiro.")

        def _worker():
            from playwright.sync_api import sync_playwright
            self._login_proc_active = True
            try:
                with self._lock:
                    with sync_playwright() as p:
                        ctx = self._launch_context(p, headless=False)
                        urls = []
                        if target in ("all", "youtube"):
                            urls.append("https://studio.youtube.com")
                        if target in ("all", "tiktok"):
                            urls.append("https://www.tiktok.com/tiktokstudio/upload")

                        page = ctx.pages[0] if ctx.pages else ctx.new_page()
                        page.goto(urls[0], wait_until="domcontentloaded")
                        for extra_url in urls[1:]:
                            p2 = ctx.new_page()
                            p2.goto(extra_url, wait_until="domcontentloaded")

                        # Wait until user closes all pages/browser window
                        while len(ctx.pages) > 0:
                            try:
                                ctx.pages[0].wait_for_timeout(500)
                            except Exception:
                                break
                        try:
                            ctx.close()
                        except Exception:
                            pass
            except Exception as e:
                logger.error(f"Browser login session error: {e}")
            finally:
                self._login_proc_active = False

        threading.Thread(target=_worker, daemon=True, name="browser-login").start()
        return {"started": True, "channel": _find_channel()}

    def publish_youtube_short(
        self, video_path: str, title: str, description: str, tags: List[str], privacy: str = "public"
    ) -> Dict[str, Any]:
        from playwright.sync_api import sync_playwright

        if "#shorts" not in title.lower():
            title = f"{title[:88]} #Shorts".strip()
        full_desc = f"{description}\n\n{' '.join(tags)}".strip()
        if "#shorts" not in full_desc.lower():
            full_desc = f"{full_desc}\n#Shorts".strip()

        with self._lock:
            with sync_playwright() as p:
                ctx = self._launch_context(p, headless=False)
                try:
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    # /upload opens YouTube Studio directly with the file picker ready
                    page.goto("https://www.youtube.com/upload", wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(3500)

                    if "accounts.google.com" in page.url:
                        raise RuntimeError(
                            "Sessão do YouTube não está logada no navegador. Clique em 'Abrir Login (YouTube & TikTok)' na aba Auto-Postagem."
                        )

                    file_input = page.locator("input[type='file']").first
                    if file_input.count() == 0:
                        create_btn = page.locator(
                            "#create-icon, ytcp-button#create-icon, button[aria-label*='Criar'], button[aria-label*='Create']"
                        ).first
                        create_btn.wait_for(state="visible", timeout=25000)
                        create_btn.click()
                        page.wait_for_timeout(800)

                        upload_item = page.locator(
                            "#text-item-0, tp-yt-paper-item:has-text('Enviar'), tp-yt-paper-item:has-text('Upload')"
                        ).first
                        upload_item.click()
                        page.wait_for_timeout(1000)
                        file_input = page.locator("input[type='file']").first

                    file_input.wait_for(state="attached", timeout=25000)
                    file_input.set_input_files(video_path)

                    # Wait for details dialog
                    title_box = page.locator("#title-textarea #textbox, div#textbox[aria-label*='título'], div#textbox[aria-label*='title']").first
                    title_box.wait_for(timeout=30000)
                    page.wait_for_timeout(1000)
                    title_box.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.type(title[:100], delay=15)

                    desc_box = page.locator("#description-textarea #textbox").first
                    if desc_box.count() > 0:
                        desc_box.click()
                        page.keyboard.press("Control+A")
                        page.keyboard.type(full_desc[:4500], delay=5)

                    # Not made for kids
                    not_kids = page.locator("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']").first
                    if not_kids.count() > 0:
                        not_kids.click()

                    # Step through Details -> Video elements -> Checks -> Visibility
                    for _ in range(3):
                        next_btn = page.locator("#next-button").first
                        next_btn.wait_for(timeout=15000)
                        next_btn.click()
                        page.wait_for_timeout(1000)

                    vis_name = {"public": "PUBLIC", "unlisted": "UNLISTED", "private": "PRIVATE"}.get(privacy, "PUBLIC")
                    vis_radio = page.locator(f"tp-yt-paper-radio-button[name='{vis_name}']").first
                    vis_radio.wait_for(timeout=15000)
                    vis_radio.click()
                    page.wait_for_timeout(800)

                    # Wait briefly if video upload is still finishing
                    done_btn = page.locator("#done-button").first
                    done_btn.wait_for(timeout=60000)
                    for _ in range(60):
                        if done_btn.get_attribute("aria-disabled") != "true":
                            break
                        page.wait_for_timeout(1500)
                    done_btn.click()
                    page.wait_for_timeout(3000)

                    link = ""
                    link_el = page.locator("a.style-scope.ytcp-video-info[href*='youtu']").first
                    if link_el.count() > 0:
                        link = link_el.get_attribute("href") or ""

                    return {"post_id": f"yt_browser_{int(time.time())}", "url": link or "https://studio.youtube.com"}
                finally:
                    ctx.close()

    def publish_tiktok(
        self, video_path: str, title: str, tags: List[str]
    ) -> Dict[str, Any]:
        from playwright.sync_api import sync_playwright

        caption = f"{title} {' '.join(tags)}".strip()[:2000]

        with self._lock:
            with sync_playwright() as p:
                ctx = self._launch_context(p, headless=False)
                try:
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    page.goto("https://www.tiktok.com/tiktokstudio/upload", wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(3500)

                    if "login" in page.url.lower():
                        raise RuntimeError(
                            "Sessão do TikTok não está logada no navegador. Clique em 'Abrir Login (YouTube & TikTok)' na aba Auto-Postagem."
                        )

                    # TikTok keeps <input type="file"> hidden behind the "Select video" button
                    file_input = page.locator("input[type='file']").first
                    file_input.wait_for(state="attached", timeout=30000)
                    file_input.set_input_files(video_path)

                    # Wait for caption editor after file upload starts
                    editor = page.locator("div.public-DraftEditor-content, div[contenteditable='true']").first
                    editor.wait_for(timeout=60000)
                    page.wait_for_timeout(2000)
                    editor.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.type(caption, delay=10)

                    # Wait for video processing / Post button to be enabled
                    post_btn = page.locator(
                        "button[data-e2e='post_video_button'], button:has-text('Postar'), button:has-text('Publicar'), button:has-text('Post')"
                    ).last
                    post_btn.wait_for(timeout=90000)
                    for _ in range(60):
                        if not post_btn.is_disabled():
                            break
                        page.wait_for_timeout(1500)

                    post_btn.click()
                    page.wait_for_timeout(4000)

                    return {"post_id": f"tt_browser_{int(time.time())}", "url": "https://www.tiktok.com/tiktokstudio/content"}
                finally:
                    ctx.close()


browser_publisher = BrowserPublisher()
