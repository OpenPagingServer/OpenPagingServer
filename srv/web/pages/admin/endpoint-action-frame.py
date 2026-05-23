
from srv.web.app import *

def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    module = request.args.get("module", "")
    action = request.args.get("action", "")
    endpoint_id = request.args.get("id", "")
    mod = load_endpoint_web(module)
    return mod.render_action(action, endpoint_id, request, db, module_page, user)
