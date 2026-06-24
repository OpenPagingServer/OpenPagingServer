
from srv.web.app import *

def action_frame_response(title, body, active="endpoints", user=None, status=200):
    return Response(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{h(title)}</title></head><body>{body}<script>
(function() {{
  function sendHeight() {{
    const body = document.body;
    const html = document.documentElement;
    const height = Math.max(
      body ? body.scrollHeight : 0,
      body ? body.offsetHeight : 0,
      html ? html.scrollHeight : 0,
      html ? html.offsetHeight : 0
    );
    if (window.parent && window.parent !== window) {{
      window.parent.postMessage({{ type: 'ops-frame-height', height: height }}, window.location.origin);
    }}
  }}
  window.addEventListener('load', sendHeight);
  window.addEventListener('resize', sendHeight);
  setTimeout(sendHeight, 0);
}})();
</script></body></html>""",
        status=status,
        mimetype="text/html",
    )

def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    module = request.args.get("module", "")
    action = request.args.get("action", "")
    endpoint_id = request.args.get("id", "")
    mod = load_endpoint_web(module)
    return mod.render_action(action, endpoint_id, request, db, action_frame_response, user)
