"""SmartHR desktop launcher: starts the bundled Ollama and the API, then opens the HR portal in the browser.

Runs from the install folder with the bundled Python. Closing this window (or Ctrl+C) stops SmartHR.
"""
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 5173  # same port as the dev portal, so the Entra redirect URI registered for it still works
OLLAMA_PORT = 11435  # not Ollama's default 11434, so an Ollama the user already runs never clashes
PORTAL = f"http://localhost:{PORT}"
HEALTH = f"http://127.0.0.1:{PORT}/health"

os.environ.update(
    OLLAMA_HOST=f"127.0.0.1:{OLLAMA_PORT}",
    OLLAMA_MODELS=str(ROOT / "ollama" / "models"),
    SMARTHR_OLLAMA_URL=f"http://127.0.0.1:{OLLAMA_PORT}",
    FASTEMBED_CACHE_PATH=str(ROOT / "models" / "fastembed"),
    HF_HOME=str(ROOT / "models" / "huggingface"),
    HF_HUB_OFFLINE="1",  # all models ship in the installer; never download at start
)


def responds(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3):
            return True
    except Exception:
        return False


def open_portal_when_ready() -> None:
    deadline = time.monotonic() + 600  # first start loads models and checks Atlas indexes
    while time.monotonic() < deadline:
        if responds(HEALTH):
            print(f"\nSmartHR is ready: {PORTAL}\nKeep this window open while you use it. Close it to stop SmartHR.\n")
            webbrowser.open(PORTAL)
            return
        time.sleep(2)
    print("SmartHR did not become ready within 10 minutes. See the messages above.")


def main() -> None:
    os.system("title SmartHR")
    if responds(HEALTH):  # already running: just show it
        webbrowser.open(PORTAL)
        return

    print("Starting SmartHR. The first start can take a minute...")
    ollama = None
    if not responds(f"http://127.0.0.1:{OLLAMA_PORT}/api/version"):  # a previous run may have left it running
        ollama = subprocess.Popen([str(ROOT / "ollama" / "ollama.exe"), "serve"], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)

    threading.Thread(target=open_portal_when_ready, daemon=True).start()
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    try:
        import uvicorn
        uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, log_level="warning")
    except (Exception, SystemExit) as error:  # uvicorn exits on a failed start; keep the window open to show why
        print(f"\nSmartHR could not start ({error!r}). It needs internet access for its MongoDB Atlas database.")
        input("Press Enter to close.")
    finally:
        if ollama:
            ollama.terminate()


if __name__ == "__main__":
    main()
