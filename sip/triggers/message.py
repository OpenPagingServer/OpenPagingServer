import subprocess
import threading
from audio_utils import generate_wav

trigger_name = "message"

def _run_script(arg):
    try:
        subprocess.run(["python3", "/opt/openpagingserver/msgsendprototype.py", str(arg)], check=False)
    except:
        pass

def handle(arg):
    def on_start():
        threading.Thread(target=_run_script, args=(arg,), daemon=True).start()

    return {
        "session_class": None,
        "generator": generate_wav("./audio/sending.wav"),
        "on_start": on_start
    }