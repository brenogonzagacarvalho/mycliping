import os
import sys
import webbrowser
import threading
import time
import uvicorn

def open_browser():
    time.sleep(1.5)
    url = "http://localhost:3107"
    print(f"\n[ClipStudio AI] Abrindo aplicativo no navegador: {url}\n")
    webbrowser.open(url)

if __name__ == "__main__":
    print("=" * 65)
    print("       ClipStudio AI - Clipador de Vídeos e Lives Local")
    print("=" * 65)
    print("Iniciando servidor local na porta 3107...")

    # Start browser auto-open thread
    threading.Thread(target=open_browser, daemon=True).start()

    # Run Uvicorn server
    uvicorn.run("app.main:app", host="0.0.0.0", port=3107, log_level="info")
