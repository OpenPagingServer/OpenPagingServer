from audio_utils import generate_tone

trigger_name = "#testtone"

def handle(arg):
    return {
        "session_class": None,
        "generator": generate_tone(1000.0, 2.0)
    }