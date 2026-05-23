from srv.web.app import *

MODULE_SETTINGS_STYLE = r"""
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
#content h1{ font-weight:400; }
.header-actions { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; gap:16px; flex-wrap:wrap; }
.back-link { color:#1976D2; text-decoration:none; display:inline-flex; align-items:center; gap:8px; }
.module-card{ background:#FFF; border:1px solid #EEE; border-radius:12px; box-shadow:0 2px 4px rgba(0,0,0,0.08); margin-bottom:16px; overflow:hidden; }
.module-head{ display:flex; justify-content:space-between; gap:18px; padding:18px; border-bottom:1px solid #EEE; align-items:flex-start; }
.module-title{ font-size:1.08em; font-weight:500; color:#202124; }
.module-meta{ color:#666; margin-top:5px; line-height:1.4; }
.module-controls{ display:flex; align-items:center; justify-content:flex-end; gap:10px; flex-wrap:wrap; min-width:240px; }
.module-status{ display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
.status-pill{ display:inline-flex; align-items:center; gap:6px; padding:5px 10px; border-radius:999px; font-size:0.82em; background:#E3F2FD; color:#1565C0; }
.status-pill.disabled{ background:#F1F3F4; color:#5F6368; }
.status-pill.error{ background:#FCE8E6; color:#A50E0E; }
.muted{ color:#777; font-size:0.92em; padding:18px; display:block; }
.success{ background:#E8F5E9; border:1px solid #A5D6A7; color:#1B5E20; padding:12px; border-radius:8px; margin-bottom:16px; }
.error{ background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; padding:12px; border-radius:8px; margin-bottom:16px; }
.module-settings-button,.toggle-button{ background:transparent; color:#1976D2; border:1px solid #1976D2; border-radius:8px; padding:9px 12px; cursor:pointer; font:inherit; display:inline-flex; align-items:center; gap:7px; text-decoration:none; min-height:38px; box-sizing:border-box; }
.module-settings-button:hover{ background:rgba(25,118,210,0.08); }
.toggle-button.enabled{ color:#C62828; border-color:#C62828; }
.toggle-button.enabled:hover{ background:rgba(198,40,40,0.08); }
.toggle-button.disabled:hover{ background:rgba(25,118,210,0.08); }
@media(max-width:767px){ .module-head{ display:grid; grid-template-columns:1fr; } .module-controls{ justify-content:flex-start; min-width:0; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.back-link{ color:#BB86FC; }
.module-card{ border-color:#333; background:#1E1E1E; }
.module-head{ border-bottom-color:#333; }
.module-title{ color:#EDEDED; }
.module-meta,.muted{ color:#BBB; }
.module-settings-button,.toggle-button.disabled{ color:#BB86FC; border-color:#BB86FC; }
.module-settings-button:hover{ background:rgba(187,134,252,0.1); }
.toggle-button.enabled{ color:#EF9A9A; border-color:#EF9A9A; }
.toggle-button.enabled:hover{ background:rgba(244,67,54,0.12); }
.toggle-button.disabled:hover{ background:rgba(187,134,252,0.1); }
.status-pill{ background:#2A2433; color:#BB86FC; }
.status-pill.disabled{ background:#2A2A2A; color:#9AA0A6; }
.status-pill.error{ background:#3B1515; color:#FFCDD2; }
.success{ background:#14351A; border-color:#2E7D32; color:#C8E6C9; }
.error{ background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
}
"""


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    modules = endpoint_module_catalog()
    messages = []
    errors = []
    if request.method == "POST":
        try:
            module = request.form.get("module", "")
            if request.form.get("action") != "toggle_module" or module not in modules:
                raise RuntimeError("Invalid module action.")
            ensure_endpoint_module_state_table()
            states = endpoint_module_state_map(modules)
            enabled = not bool(states.get(module))
            execute(
                "INSERT INTO endpointmodulesloaded (`dir`, enabled) VALUES (%s,%s) ON DUPLICATE KEY UPDATE enabled=VALUES(enabled)",
                (module, "true" if enabled else "false"),
            )
            messages.append(f"{modules[module].get('name') or module} {'enabled' if enabled else 'disabled'}.")
            modules = endpoint_module_catalog()
        except Exception as exc:
            errors.append(str(exc))
    notices = "".join(f'<div class="success">{h(message)}</div>' for message in messages)
    notices += "".join(f'<div class="error">{h(error)}</div>' for error in errors)
    cards = ""
    for module, module_info in modules.items():
        is_enabled = bool(module_info.get("enabled"))
        settings_link = ""
        if module_info.get("has_settings_page"):
            settings_link = f"""<a class="module-settings-button" href="/admin/endpoint-module-settings-configure?{h(urlencode({"module": module}))}">
                            <i class="fa-solid fa-sliders"></i> Module Settings
                        </a>"""
        description = f'<div class="module-meta">{h(module_info.get("description") or "")}</div>' if module_info.get("description") else ""
        version = f' - Version {h(module_info.get("version"))}' if module_info.get("version") else ""
        cards += f"""<section class="module-card">
            <div class="module-head">
                <div>
                    <div class="module-title">{h(module_info.get("name") or module)}</div>
                    <div class="module-meta">{h(module)}{f' - {h(module_info.get("input_type"))}' if module_info.get("input_type") else ''}{version}</div>
                    {description}
                </div>
                <div class="module-controls">
                    {settings_link}
                    <form method="post">
                        <input type="hidden" name="action" value="toggle_module">
                        <input type="hidden" name="module" value="{h(module)}">
                        <button class="toggle-button {'enabled' if is_enabled else 'disabled'}" type="submit">
                            <i class="fa-solid {'fa-toggle-on' if is_enabled else 'fa-toggle-off'}"></i>
                            {'Disable' if is_enabled else 'Enable'}
                        </button>
                    </form>
                </div>
            </div>
        </section>"""
    if not cards:
        cards = '<p class="muted">No endpoint modules found.</p>'
    content = f"""    <div class="header-actions">
        <h1>Endpoint Module Settings</h1>
        <a class="back-link" href="/admin/manage-endpoints"><i class="fa-solid fa-arrow-left"></i> Back</a>
    </div>
    {notices}
    {cards}"""
    return legacy_page("Endpoint Module Settings", legacy_user_context(user), "endpoints", MODULE_SETTINGS_STYLE, content)
