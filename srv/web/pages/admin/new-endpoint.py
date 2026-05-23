from srv.web.app import *

NEW_ENDPOINT_STYLE = r"""
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
.module-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:14px; }
.module-card { background:#FFF; border:1px solid #EEE; border-radius:12px; box-shadow:0 2px 4px rgba(0,0,0,0.08); padding:18px; display:flex; flex-direction:column; gap:10px; text-decoration:none; color:inherit; min-height:168px; }
.module-card:focus,.module-card:hover { border-color:#1976D2; box-shadow:0 0 0 2px rgba(25,118,210,0.12); outline:none; }
.module-top { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }
.module-name { font-size:1.08em; font-weight:500; color:#202124; }
.module-badges { display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }
.module-badge { align-self:flex-start; background:#E3F2FD; color:#1565C0; border-radius:999px; padding:4px 10px; font-size:0.82em; }
.module-badge.disabled { background:#F1F3F4; color:#5F6368; }
.module-description { color:#444; line-height:1.45; flex:1; }
.module-meta { color:#666; font-size:0.9em; line-height:1.35; }
.muted { color:#777; font-size:0.92em; }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.back-link { color:#BB86FC; }
.module-card{ border:1px solid #333; background-color:#1E1E1E; }
.module-card:focus,.module-card:hover { border-color:#BB86FC; box-shadow:0 0 0 2px rgba(187,134,252,0.16); }
.module-name { color:#EDEDED; }
.module-description,.module-meta,.muted{ color:#BBB; }
.module-badge { background:#2A2433; color:#BB86FC; }
.module-badge.disabled { background:#2A2A2A; color:#9AA0A6; }
}
"""


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    ctx = legacy_user_context(user)
    modules = [info for info in endpoint_module_catalog().values() if info.get("has_forms")]
    if modules:
        cards = []
        for module in modules:
            meta_parts = []
            if module.get("version"):
                meta_parts.append(f"Version {h(module['version'])}")
            meta = f'<div class="module-meta">{" - ".join(meta_parts)}</div>' if meta_parts else ""
            desc = f'<div class="module-description">{h(module.get("description") or "")}</div>'
            state_badge = '<span class="module-badge disabled">Disabled</span>' if not module.get("enabled") else ""
            cards.append(
                f"""<a class="module-card" href="/admin/new-endpoint-configure?module={h(module["module"])}">
                    <div class="module-top">
                        <div class="module-name">{h(module.get("name") or module["module"])}</div>
                        <div class="module-badges">
                            <span class="module-badge">{h(module.get("input_type") or "Output")}</span>
                            {state_badge}
                        </div>
                    </div>
                    {meta}
                    {desc}
                </a>"""
            )
        body = '<div class="module-grid">' + "".join(cards) + "</div>"
    else:
        body = '<p class="muted">No endpoint modules with add forms were found.</p>'
    content = f"""    <div class="header-actions">
        <h1>New Endpoint</h1>
        <a class="back-link" href="/admin/manage-endpoints"><i class="fa-solid fa-arrow-left"></i> Back</a>
    </div>
    {body}"""
    return legacy_page("New Endpoint", ctx, "endpoints", NEW_ENDPOINT_STYLE, content)
