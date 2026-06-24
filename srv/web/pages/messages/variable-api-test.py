from broadcasts import message_variable_api_fetch
from srv.web.app import *


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    if request.method != "POST":
        abort(405)
    payload = request.get_json(silent=True) or {}
    url = str(payload.get("url") or request.form.get("url") or "").strip()
    if not url:
        return jsonify(ok=False, error="URL is required."), 400
    result = message_variable_api_fetch(url, show_online_docs=show_online_docs_on_error_page())
    status_code = int(result.get("status_code") or 0)
    if status_code > 0:
        return jsonify(
            ok=True,
            status_code=status_code,
            result=result.get("body") or "",
            error=result.get("error") or "",
        )
    return jsonify(
        ok=False,
        status_code=0,
        result=result.get("body") or "",
        error=result.get("error") or "API test failed.",
    ), 502
