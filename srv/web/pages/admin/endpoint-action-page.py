from srv.web.app import *

ACTION_STYLE = r"""
body, html { margin:0; padding:0; font-family:"Tahoma",sans-serif; font-weight:300; background-color:#FFF; height:100%; }
#sidebar { width:220px; background-color:#1976D2; color:#FFF; height:100vh; position:fixed; top:0; left:0; display:flex; flex-direction:column; box-shadow:2px 0 8px rgba(0,0,0,0.2); transition:transform 0.3s ease; z-index:1200; }
@media (max-width:767px){ #sidebar{ transform:translateX(-100%); } #sidebar.open{ transform:translateX(0); } }
#sidebar h2 { text-align:center; padding:20px 0; margin:0; font-weight:500; background-color:#1565C0; font-size:1.2em; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#FFF; padding:12px 20px; display:block; border-bottom:1px solid rgba(255,255,255,0.1); text-decoration:none; transition:background 0.3s; font-size:0.9em; text-align:left; box-sizing:border-box; }
#sidebar a i,.logout-btn i,.logout-btn-mobile i,.admin-only i { margin-right:8px; width:20px; }
#sidebar a:hover,#sidebar a.active{ background-color:#1565C0; }
.logout-btn{ background-color:#C62828; border:none; cursor:pointer; margin-top:auto; transition:background-color 0.3s; }
.logout-btn-mobile{ background-color:#C62828; border:none; cursor:pointer; transition:background-color 0.3s; display:none; }
@media(max-width:767px){ .logout-btn{ display:none; } .logout-btn-mobile{ display:block; } }
#mobile-header{ display:flex; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; min-height:100vh; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; margin-bottom:4px; }
.header-actions { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:20px; gap:16px; flex-wrap:wrap; }
.back-link { color:#1976D2; text-decoration:none; display:inline-flex; align-items:center; gap:8px; margin-top:8px; }
.muted { color:#666; margin-top:0; }
.endpoint-id { color:#555; font-size:0.92em; overflow-wrap:anywhere; }
.frame-shell { background:#FFF; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); padding:12px; box-sizing:border-box; }
.form-frame { width:100%; min-height:620px; border:0; border-radius:6px; background:#FFF; box-sizing:border-box; display:block; }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.back-link { color:#BB86FC; }
.muted,.endpoint-id{ color:#BBB; }
.frame-shell { border-color:#333; background:#1E1E1E; box-shadow:none; }
.form-frame { background:#1E1E1E; }
}
"""


def safe_name(value):
    return re.fullmatch(r"[A-Za-z0-9_-]+", str(value or "")) is not None


def render_endpoint_action_frame(action):
    user = require_admin()
    if not isinstance(user, dict):
        return user
    if demo_mode_enabled():
        return demo_mode_iframe_html("manage-endpoints")
    module = request.args.get("module", "")
    endpoint_id = request.args.get("id", "").strip()
    if action not in {"edit", "delete"} or not safe_name(module) or endpoint_id == "" or len(endpoint_id) > 255:
        abort(400)
    mod = load_endpoint_web(module)
    return mod.render_action(action, endpoint_id, request, db, module_page, user)


def render_endpoint_action_page(action):
    if request.path.rstrip("/").endswith("endpoint-action-frame"):
        return render_endpoint_action_frame(request.args.get("action", action))
    user = require_admin()
    if not isinstance(user, dict):
        return user
    if demo_mode_enabled():
        return demo_mode_page("Manage Endpoints", legacy_user_context(user), "endpoints", "manage-endpoints")
    module = request.args.get("module", "")
    endpoint_id = request.args.get("id", "").strip()
    if action not in {"edit", "delete"} or not safe_name(module) or endpoint_id == "" or len(endpoint_id) > 255:
        abort(400)
    info = endpoint_module_catalog(include_system=True).get(module)
    if not info or not info.get("can_load", True):
        abort(404)
    action_title = "Delete Endpoint" if action == "delete" else "Edit Endpoint"
    frame_src = "/admin/endpoint-action-frame?" + urlencode({"action": action, "module": module, "id": endpoint_id})
    content = f"""    <div class="header-actions">
        <div>
            <h1>{h(action_title)}</h1>
            <p class="muted">{h(info.get("name") or module)}</p>
            <div class="endpoint-id">{h(endpoint_id)}</div>
        </div>
        <a class="back-link" href="/admin/manage-endpoints"><i class="fa-solid fa-arrow-left"></i> Endpoints</a>
    </div>
    <div class="frame-shell">
        <iframe class="form-frame" sandbox="allow-forms allow-same-origin allow-scripts allow-top-navigation" src="{h(frame_src)}" title="{h(action_title)}"></iframe>
    </div>"""
    return legacy_page(action_title, legacy_user_context(user), "endpoints", ACTION_STYLE, content)


def handle_request():
    return render_endpoint_action_page(request.args.get("action", "edit"))
