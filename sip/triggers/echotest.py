from audio_utils import EchoRTPSession

trigger_name = "#echotest"

def handle(arg):
    return {
        "session_class": EchoRTPSession,
        "generator": None
    }