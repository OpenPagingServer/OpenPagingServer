from srv.web.app import *

MODULE_CONFIGURE_STYLE = r"""
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
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; min-height:100vh; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; margin-bottom:4px; }
.header-actions { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:20px; gap:16px; flex-wrap:wrap; }
.back-link { color:#1976D2; text-decoration:none; display:inline-flex; align-items:center; gap:8px; margin-top:8px; }
.muted { color:#666; margin-top:0; }
.settings-frame { width:100%; min-height:760px; border:1px solid #EEE; border-radius:8px; background:#FFF; box-sizing:border-box; }
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
.muted{ color:#BBB; }
.settings-frame { border-color:#333; background:#1E1E1E; }
}
"""


def module_safe_name(value):
    return re.fullmatch(r"[A-Za-z0-9_-]+", str(value or "")) is not None


def module_settings_response(title, body, active="endpoints", user=None, status=200):
    return Response(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{h(title)}</title></head><body>{body}</body></html>""",
        status=status,
        mimetype="text/html",
    )


def response_body(response):
    text = response.get_data(as_text=True) if isinstance(response, Response) else str(response)
    match = re.search(r"<body[^>]*>(.*)</body>", text, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else text


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    module = request.args.get("module", "")
    info = endpoint_module_catalog().get(module)
    if not module_safe_name(module) or not info or not info.get("can_load", True) or not info.get("has_settings_page"):
        abort(404)
    mod = load_endpoint_web(module)
    if getattr(mod, "render_settings", None) is None:
        abort(404)
    description = f'<p class="muted">{h(info.get("description") or "")}</p>' if info.get("description") else ""
    rendered = mod.render_settings(request, db, module_settings_response, user)
    settings_body = response_body(rendered)
    content = f"""    <div class="header-actions">
        <div>
            <h1>{h(info.get("name") or module)} Settings</h1>
            {description}
        </div>
        <a class="back-link" href="/admin/endpoint-module-settings"><i class="fa-solid fa-arrow-left"></i> Manage Endpoint Modules</a>
    </div>
    <div class="settings-frame" style="padding:18px; overflow:auto;">{settings_body}</div>"""
    return legacy_page(f"{info.get('name') or module} Settings", legacy_user_context(user), "endpoints", MODULE_CONFIGURE_STYLE, content)
