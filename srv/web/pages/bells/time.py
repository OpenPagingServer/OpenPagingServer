from srv.web.app import *

def handle_request():
    now = datetime.now().astimezone()
    return jsonify(
        timestamp_ms=int(now.timestamp() * 1000),
        uses_12_hour=False,
        timezone="",
    )
