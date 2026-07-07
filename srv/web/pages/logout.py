
from srv.web.app import *

def handle_request():
    session_id = str(session.get("web_session_id") or "").strip()
    user_id = session.get("user_id")
    if session_id:
        revoke_user_session_record(session_id, user_id)
    session.clear()
    return redirect("/index")
