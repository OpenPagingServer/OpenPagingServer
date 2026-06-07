from srv.web.app import *

ENDPOINTS_STYLE = r"""
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
#content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; }
.header-actions { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; gap:16px; flex-wrap:wrap; }
.sort-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.sort-link { color:#1976D2; text-decoration:none; padding:8px 10px; border-radius:4px; border:1px solid #EEE; font-size:0.9em; }
.sort-link.active { background:#1976D2; color:#FFF; border-color:#1976D2; }
.settings-button { color:#1976D2; text-decoration:none; padding:9px 12px; border-radius:4px; border:1px solid #1976D2; font-size:0.9em; display:inline-flex; align-items:center; gap:7px; }
.settings-button:hover { background:rgba(25,118,210,0.08); }
.add-button { width:40px; height:40px; border-radius:50%; background:#1976D2; color:#FFF; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; box-shadow:0 2px 5px rgba(0,0,0,0.24); transition:background-color 0.2s, box-shadow 0.2s; }
.add-button:hover { background:#1565C0; box-shadow:0 4px 8px rgba(0,0,0,0.28); }
.info-card{ background:#FFF; padding:0; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; overflow:hidden; }
.summary-grid { display:grid; grid-template-columns:minmax(180px,280px); gap:12px; margin-bottom:16px; }
.summary-item { border:1px solid #EEE; border-radius:8px; padding:12px; background:#FFF; }
.summary-item strong { display:block; font-size:1.4em; font-weight:500; }
.muted { color:#777; font-size:0.9em; }
.endpoint-list { list-style:none; margin:0; padding:0; }
.endpoint-item { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:16px 18px; border-bottom:1px solid #EEE; background:#FFF; transition:background-color 0.2s, box-shadow 0.2s; }
.endpoint-item:last-child { border-bottom:none; }
.endpoint-item:hover { background:#F8F8F8; }
.endpoint-main { min-width:0; }
.endpoint-name { font-size:1.05em; font-weight:500; color:#202124; overflow-wrap:anywhere; }
.endpoint-meta { color:#555; margin-top:4px; }
.endpoint-status { display:flex; align-items:center; gap:7px; margin-top:6px; color:#666; font-size:0.92em; }
.status-dot { width:10px; height:10px; border-radius:50%; background:#9E9E9E; flex:0 0 10px; }
.status-dot.online, .status-dot.configured { background:#2E7D32; }
.status-dot.offline { background:#C62828; }
.status-dot.unchecked, .status-dot.unknown { background:#9E9E9E; }
.endpoint-actions { display:flex; align-items:center; gap:4px; flex:0 0 auto; }
.icon-action { width:36px; height:36px; border-radius:50%; color:#555; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; transition:background-color 0.2s, color 0.2s; }
.icon-action:hover { background:rgba(25,118,210,0.08); color:#1976D2; }
.icon-action.delete:hover { background:rgba(198,40,40,0.08); color:#C62828; }
.success { background:#E8F5E9; border:1px solid #A5D6A7; color:#1B5E20; padding:12px; border-radius:8px; margin-bottom:16px; }
.error { background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; padding:12px; border-radius:8px; margin-bottom:16px; }
@media(max-width:767px){ .endpoint-item{ align-items:flex-start; } .endpoint-actions{ flex-direction:column; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.info-card,.summary-item{ border:1px solid #333; background-color:#1E1E1E; }
.muted{ color:#BBB; }
.sort-link { color:#BB86FC; border-color:#333; }
.sort-link.active { background:#BB86FC; color:#000; border-color:#BB86FC; }
.settings-button { color:#BB86FC; border-color:#BB86FC; }
.settings-button:hover { background:rgba(187,134,252,0.1); }
.add-button { background:#BB86FC; color:#000; }
.add-button:hover { background:#A370F7; }
.endpoint-item { background:#1E1E1E; border-bottom:1px solid #333; }
.endpoint-item:hover { background:#242424; }
.endpoint-name { color:#EDEDED; }
.endpoint-meta { color:#CCC; }
.endpoint-status { color:#BBB; }
.icon-action { color:#BBB; }
.icon-action:hover { background:rgba(187,134,252,0.1); color:#BB86FC; }
.icon-action.delete:hover { background:rgba(244,67,54,0.12); color:#EF9A9A; }
.success { background:#14351A; border-color:#2E7D32; color:#C8E6C9; }
.error { background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
}
"""


def cmp_key_text(value):
    return str(value or "").strip().lower()


def endpoint_display_line(endpoint):
    module = str(endpoint.get("module_display") or "").strip()
    endpoint_type = str(endpoint.get("type") or "").strip()
    model = str(endpoint.get("model") or "").strip()
    if endpoint.get("module") == "siptrunks":
        label = endpoint_type or model or "SIP Trunk"
        if label == "SIP Trunk Extension":
            return label
        return f"{label} ({module})" if module else label
    model_suffix = model
    if module and model_suffix.lower().startswith((module + " ").lower()):
        model_suffix = model_suffix[len(module):].strip()
    elif model_suffix.lower() == module.lower():
        model_suffix = ""
    label = endpoint_type if module and endpoint_type.lower().startswith(module.lower()) else f"{module} {endpoint_type}".strip()
    if model_suffix and model_suffix.lower() not in label.lower():
        label = f"{label} {model_suffix}".strip()
    return f"{label} ({module})" if module else label


def endpoint_rows(payload):
    rows = []
    module_errors = []
    for module_info in payload.get("modules") or []:
        module_name = module_info.get("module") or ""
        display_name = module_info.get("display_name") or module_name
        endpoints = module_info.get("endpoints") or []
        if module_info.get("error"):
            module_errors.append(f"{display_name}: {module_info['error']}".strip())
        for endpoint in endpoints:
            rows.append(
                {
                    "module": module_name,
                    "module_display": display_name,
                    "module_count": len(endpoints),
                    "id": endpoint.get("id") or "",
                    "name": endpoint.get("name") or "",
                    "model": endpoint.get("model") or "",
                    "address": endpoint.get("address") or "",
                    "status": endpoint.get("status") or "",
                    "type": endpoint.get("type") or "",
                }
            )
    return rows, module_errors


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    ctx = legacy_user_context(user)
    demo = demo_mode_enabled()
    sort = request.args.get("sort", "alpha")
    if sort not in {"alpha", "module", "devices"}:
        sort = "alpha"
    payload = endpoint_ipc("LIST_ENDPOINTS")
    rows, module_errors = endpoint_rows(payload)
    if sort == "module":
        rows.sort(key=lambda row: (cmp_key_text(row["module_display"]), cmp_key_text(row["name"] or row["id"] or row["address"]), cmp_key_text(row["address"])))
    elif sort == "devices":
        rows.sort(key=lambda row: (-int(row["module_count"] or 0), cmp_key_text(row["module_display"]), cmp_key_text(row["name"] or row["id"] or row["address"])))
    else:
        rows.sort(key=lambda row: (cmp_key_text(row["name"] or row["id"] or row["address"]), cmp_key_text(row["address"]), cmp_key_text(row["module_display"])))

    notices = ""
    success = session.pop("endpoint_flash_success", "")
    if success:
        notices += f'<div class="success">{h(success)}</div>'
    if not payload.get("ok", True):
        notices += f'<div class="error">{h(payload.get("error") or "Endpoint manager unavailable")}</div>'
    if payload.get("warning"):
        notices += f'<div class="error">{h(payload.get("warning"))}</div>'
    notices += "".join(f'<div class="error">{h(err)}</div>' for err in module_errors if err)
    sort_links = "".join(
        f'<a class="sort-link {"active" if sort == key else ""}" href="?sort={key}">{label}</a>'
        for key, label in (("alpha", "Alphabetical"), ("module", "Module"), ("devices", "Most Devices"))
    )
    new_href = "javascript:openDemoModePopup('manage-endpoints')" if demo else "/admin/new-endpoint"
    settings_href = "javascript:openDemoModePopup('manage-endpoints')" if demo else "/admin/endpoint-module-settings"
    if rows:
        rendered_rows = []
        for endpoint in rows:
            endpoint_id = endpoint.get("id") or endpoint.get("name") or endpoint.get("address")
            query = urlencode({"module": endpoint.get("module"), "id": endpoint_id})
            status = str(endpoint.get("status") or "")
            address = endpoint.get("address")
            status_html = ""
            if status:
                status_token = status.split(" ", 1)[0].strip("(),")
                status_class = re.sub(r"[^a-z0-9]+", "-", status_token.lower())
                status_text = h(status) + (f" ({h(address)})" if address else "")
                status_html = f'<div class="endpoint-status"><span class="status-dot {h(status_class)}"></span><span>{status_text}</span></div>'
            edit_href = "javascript:openDemoModePopup('manage-endpoints')" if demo else f"/admin/edit-endpoint?{h(query)}"
            delete_href = "javascript:openDemoModePopup('manage-endpoints')" if demo else f"/admin/delete-endpoint?{h(query)}"
            rendered_rows.append(
                f"""<li class="endpoint-item">
                        <div class="endpoint-main">
                            <div class="endpoint-name">{h(endpoint.get("name") or endpoint_id)}</div>
                            <div class="endpoint-meta">{h(endpoint_display_line(endpoint))}</div>
                            {status_html}
                        </div>
                        <div class="endpoint-actions">
                            <a class="icon-action" href="{edit_href}" title="Edit"><i class="fa-solid fa-pen-to-square"></i></a>
                            <a class="icon-action delete" href="{delete_href}" title="Delete"><i class="fa-solid fa-trash"></i></a>
                        </div>
                    </li>"""
            )
        endpoint_list = '<ul class="endpoint-list">' + "".join(rendered_rows) + "</ul>"
    else:
        endpoint_list = '<p class="muted" style="text-align:center; padding:20px;">No endpoints found</p>'
    content = f"""    <div class="header-actions">
        <h1>Manage Endpoints</h1>
        <div class="sort-actions">
            <a class="add-button" href="{new_href}" title="New Endpoint"><i class="fa-solid fa-plus"></i></a>
            <a class="settings-button" href="{settings_href}"><i class="fa-solid fa-sliders"></i> Endpoint Module Settings</a>
            <span class="muted">Sort</span>
            {sort_links}
        </div>
    </div>
    {notices}
    <div class="summary-grid">
        <div class="summary-item"><strong>{h(len(rows))}</strong><span class="muted">Endpoints</span></div>
    </div>
    <div class="info-card">{endpoint_list}</div>"""
    return legacy_page("Manage Endpoints", ctx, "endpoints", ENDPOINTS_STYLE, content)
