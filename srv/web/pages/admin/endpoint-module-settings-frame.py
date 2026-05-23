from srv.web.app import *


def frame_response(title, body, active="endpoints", user=None, status=200):
    return Response(
        f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{h(title)}</title></head>
<body>{body}</body>
</html>""",
        status=status,
        mimetype="text/html",
    )


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    module = request.args.get("module", "")
    mod = load_endpoint_web(module)
    renderer = getattr(mod, "render_settings", None)
    if renderer is None:
        return Response("""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#666;background:#fff}@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#bbb}}</style></head><body>No module settings.</body></html>""", mimetype="text/html")
    return renderer(request, db, frame_response, user)
