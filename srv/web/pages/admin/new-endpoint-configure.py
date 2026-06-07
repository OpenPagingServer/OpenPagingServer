from srv.web.app import *

CONFIGURE_STYLE = r"""
body, html { margin:0; padding:0; font-family:"Tahoma",sans-serif; font-weight:300; background-color:#FFF; height:100%; }
#sidebar { width:220px; background-color:#1976D2; color:#FFF; height:100vh; position:fixed; top:0; left:0; display:flex; flex-direction:column; box-shadow:2px 0 8px rgba(0,0,0,0.2); transition:transform 0.3s ease; z-index:1200; }
@media (max-width:767px){ #sidebar{ transform:translateX(-100%); } #sidebar.open{ transform:translateX(0); } }
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
.frame-shell { background:#FFF; border:1px solid #EEE; border-radius:10px; box-shadow:0 2px 4px rgba(0,0,0,0.08); padding:12px; box-sizing:border-box; }
.form-frame { width:100%; min-height:760px; border:0; border-radius:8px; background:#FFF; box-sizing:border-box; display:block; }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.back-link { color:#BB86FC; }
.muted{ color:#BBB; }
.frame-shell { border-color:#333; background:#1E1E1E; box-shadow:none; }
.form-frame { background:#1E1E1E; }
}
"""


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    if demo_mode_enabled():
        return demo_mode_page("Add Endpoint", legacy_user_context(user), "endpoints", "manage-endpoints")
    module = request.args.get("module", "")
    module_info = endpoint_module_catalog(include_system=True).get(module)
    if not safe_module_name(module) or not module_info or not module_info.get("has_forms") or not module_info.get("can_load", True):
        abort(404)
    ctx = legacy_user_context(user)
    description = f'<p class="muted">{h(module_info.get("description") or "")}</p>' if module_info.get("description") else ""
    frame_src = "/admin/endpoint-form-frame?" + urlencode({"module": module})
    content = f"""    <div class="header-actions">
        <div>
            <h1>{h(module_info.get("name") or module)} Endpoint</h1>
            {description}
        </div>
        <a class="back-link" href="/admin/new-endpoint"><i class="fa-solid fa-arrow-left"></i> Modules</a>
    </div>
    <div class="frame-shell">
        <iframe class="form-frame" sandbox="allow-forms allow-same-origin allow-scripts allow-top-navigation" src="{h(frame_src)}" title="{h(module_info.get("name") or module)} endpoint form"></iframe>
    </div>"""
    return legacy_page(f"Add {module_info.get('name') or module} Endpoint", ctx, "endpoints", CONFIGURE_STYLE, content)
