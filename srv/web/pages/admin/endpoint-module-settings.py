import os
import tempfile

from srv.web.app import *

MAX_MODULE_UPLOAD_BYTES = 100 * 1024 * 1024
MODULE_UPLOAD_TMP_DIR = Path(os.getenv("OPS_ENDPOINT_MODULE_UPLOAD_TMP", "/tmp"))

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
.header-actions h1{ margin:0; }
.toolbar{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.back-link { color:#1976D2; text-decoration:none; display:inline-flex; align-items:center; gap:8px; }
.button{ background:#1A73E8; color:#FFF; border:none; border-radius:999px; padding:10px 16px; cursor:pointer; font:inherit; display:inline-flex; align-items:center; gap:8px; text-decoration:none; box-shadow:0 1px 2px rgba(60,64,67,.25); }
.button:hover{ background:#1765CC; }
.button.subtle{ background:#FFF; color:#1A73E8; border:1px solid #DADCE0; box-shadow:none; border-radius:8px; }
.control{ padding:10px; border:1px solid #DADCE0; border-radius:8px; font:inherit; box-sizing:border-box; background:#FFF; color:#202124; }
.module-card{ background:#FFF; border:1px solid #EEE; border-radius:12px; box-shadow:0 2px 4px rgba(0,0,0,0.08); margin-bottom:16px; overflow:hidden; }
.module-head{ display:flex; justify-content:space-between; gap:18px; padding:18px; border-bottom:1px solid #EEE; align-items:flex-start; }
.module-title{ font-size:1.08em; font-weight:500; color:#202124; }
.module-meta{ color:#666; margin-top:5px; line-height:1.4; }
.module-controls{ display:flex; align-items:center; justify-content:flex-end; gap:10px; flex-wrap:wrap; min-width:240px; }
.module-status{ display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
.status-pill{ display:inline-flex; align-items:center; gap:6px; padding:5px 10px; border-radius:999px; font-size:0.82em; background:#E3F2FD; color:#1565C0; }
.status-pill.disabled{ background:#F1F3F4; color:#5F6368; }
.status-pill.error{ background:#FCE8E6; color:#A50E0E; }
.status-pill.signed{ background:#E8F5E9; color:#1B5E20; }
.cannot-load{ color:#A50E0E; font-weight:500; }
.muted{ color:#777; font-size:0.92em; padding:18px; display:block; }
.success{ background:#E8F5E9; border:1px solid #A5D6A7; color:#1B5E20; padding:12px; border-radius:8px; margin-bottom:16px; }
.error{ background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; padding:12px; border-radius:8px; margin-bottom:16px; }
.module-settings-button,.toggle-button{ background:transparent; color:#1976D2; border:1px solid #1976D2; border-radius:8px; padding:9px 12px; cursor:pointer; font:inherit; display:inline-flex; align-items:center; gap:7px; text-decoration:none; min-height:38px; box-sizing:border-box; }
.module-settings-button:hover{ background:rgba(25,118,210,0.08); }
.toggle-button.enabled{ color:#C62828; border-color:#C62828; }
.toggle-button.enabled:hover{ background:rgba(198,40,40,0.08); }
.toggle-button.disabled:hover{ background:rgba(25,118,210,0.08); }
.modal-backdrop{ display:none; position:fixed; inset:0; background:rgba(32,33,36,.55); z-index:2000; align-items:center; justify-content:center; padding:20px; box-sizing:border-box; }
.modal-backdrop.active{ display:flex; }
.modal-card{ width:100%; max-width:460px; background:#FFF; border-radius:18px; box-shadow:0 20px 60px rgba(0,0,0,.28); overflow:hidden; }
.modal-header{ display:flex; align-items:center; justify-content:space-between; padding:18px 20px; border-bottom:1px solid #E8EAED; }
.modal-header h2{ margin:0; font-weight:400; font-size:1.25em; }
.modal-close{ border:none; background:transparent; width:36px; height:36px; border-radius:50%; cursor:pointer; color:#5F6368; font-size:1.1em; }
.modal-close:hover{ background:#F1F3F4; }
.modal-body{ padding:20px; }
.upload-box{ border:2px dashed #DADCE0; border-radius:16px; padding:22px; text-align:center; background:#F8FAFD; }
.upload-box input{ width:100%; margin-top:14px; }
.modal-actions{ display:flex; justify-content:flex-end; gap:10px; padding:16px 20px; border-top:1px solid #E8EAED; }
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
.button{ background:#BB86FC; color:#000; }
.button:hover{ background:#A874E8; }
.button.subtle{ background:transparent; color:#BB86FC; border-color:#BB86FC; }
.control{ background:#171717; border-color:#444; color:#EEE; }
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
.status-pill.signed{ background:#14351A; color:#C8E6C9; }
.cannot-load{ color:#FFCDD2; }
.success{ background:#14351A; border-color:#2E7D32; color:#C8E6C9; }
.error{ background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
.modal-card{ background:#1E1E1E; border-color:#333; }
.upload-box{ background:#2A2A2A; color:#BBB; border-color:#444; }
.modal-close{ color:#BBB; }
.modal-close:hover{ background:#333; }
.modal-header,.modal-actions{ border-color:#333; }
}
"""

MODULE_SETTINGS_SCRIPT = r"""
function openUploadModal() {
  const modal = document.getElementById("upload-modal");
  if (modal) modal.classList.add("active");
}
function closeUploadModal() {
  const modal = document.getElementById("upload-modal");
  if (modal) modal.classList.remove("active");
}
function modalBackdropClick(event) {
  if (event.target.id === "upload-modal") closeUploadModal();
}
document.addEventListener("keydown", function(event) {
  if (event.key === "Escape") closeUploadModal();
});
"""


def format_bytes(value):
    value = int(value or 0)
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def clean_module_upload_name(name):
    name = Path(str(name or "")).name
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def validate_uploaded_endpoint_module(temp_path, original_name):
    if Path(original_name).suffix.lower() != ".opsepm":
        raise RuntimeError("Only .opsepm endpoint module packages are allowed.")
    if not temp_path.is_file() or temp_path.stat().st_size <= 0:
        raise RuntimeError("Choose a non-empty endpoint module package.")
    if temp_path.stat().st_size > MAX_MODULE_UPLOAD_BYTES:
        raise RuntimeError(f"Endpoint module package is too large. Limit: {format_bytes(MAX_MODULE_UPLOAD_BYTES)}.")
    with open(temp_path, "rb") as handle:
        if handle.read(2) != b"\x1f\x8b":
            raise RuntimeError("The uploaded file is not a valid .opsepm package.")
    package = endpoints.endpoint_package_info(temp_path, extract_if_trusted=True)
    if not package.get("trusted"):
        raise RuntimeError(package.get("load_error") or "This module is not signed by a trusted CA.")
    payload_path = Path(package.get("payload_path") or "")
    if not payload_path.is_dir() or not (payload_path / "index.py").is_file():
        raise RuntimeError("Endpoint module package is missing a valid payload/index.py file.")
    manifest = package.get("manifest") or {}
    module = manifest.get("module") or ""
    if not endpoints.safe_module_name(module):
        raise RuntimeError("Endpoint module manifest contains an invalid module id.")
    return package


def save_uploaded_endpoint_module(item):
    if item is None or not item.filename:
        raise RuntimeError("Choose an endpoint module package to upload.")
    clean_name = clean_module_upload_name(item.filename)
    MODULE_UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix="ops-endpoint-module-", suffix=".opsepm", dir=str(MODULE_UPLOAD_TMP_DIR))
    os.close(handle)
    temp_path = Path(tmp_name)
    try:
        item.save(temp_path)
        package = validate_uploaded_endpoint_module(temp_path, clean_name)
        manifest = package.get("manifest") or {}
        module = manifest["module"]
        endpoints.MODULE_STORE_DIR.mkdir(parents=True, exist_ok=True)
        target = (endpoints.MODULE_STORE_DIR / f"{module}.opsepm").resolve()
        store_root = endpoints.MODULE_STORE_DIR.resolve()
        if store_root not in target.parents:
            raise RuntimeError("Invalid endpoint module install path.")
        temp_path.replace(target)
        endpoints.upsert_module_package_registry(endpoints.discover_endpoint_packages(extract_if_trusted=False))
        return manifest.get("name") or module
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def module_has_render_settings(module):
    try:
        mod = load_endpoint_web(module)
        return getattr(mod, "render_settings", None) is not None
    except Exception:
        return False


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    if demo_mode_enabled():
        return demo_mode_page("Manage Endpoint Modules", legacy_user_context(user), "endpoints", "manage-endpoints")
    modules = endpoint_module_catalog()
    messages = []
    errors = []
    if request.method == "POST":
        try:
            action = request.form.get("action", "")
            if action == "upload_module":
                display_name = save_uploaded_endpoint_module(request.files.get("module_file"))
                messages.append(f"{display_name} uploaded.")
                modules = endpoint_module_catalog()
            elif action == "toggle_module":
                module = request.form.get("module", "")
                if module not in modules:
                    raise RuntimeError("Invalid module action.")
                if not modules[module].get("can_load", True):
                    raise RuntimeError("This module cannot be loaded.")
                ensure_endpoint_module_state_table()
                states = endpoint_module_state_map(modules)
                enabled = not bool(states.get(module))
                execute(
                    "INSERT INTO endpointmodulesloaded (`dir`, enabled) VALUES (%s,%s) ON DUPLICATE KEY UPDATE enabled=VALUES(enabled)",
                    (module, "true" if enabled else "false"),
                )
                messages.append(f"{modules[module].get('name') or module} {'enabled' if enabled else 'disabled'}.")
                modules = endpoint_module_catalog()
            else:
                raise RuntimeError("Invalid module action.")
        except Exception as exc:
            errors.append(str(exc))
    notices = "".join(f'<div class="success">{h(message)}</div>' for message in messages)
    notices += "".join(f'<div class="error">{h(error)}</div>' for error in errors)
    cards = ""
    for module, module_info in modules.items():
        is_enabled = bool(module_info.get("enabled"))
        can_load = bool(module_info.get("can_load", True))
        settings_link = ""
        if can_load and module_info.get("has_settings_page") and module_has_render_settings(module):
            settings_link = f"""<a class="module-settings-button" href="/admin/endpoint-module-settings-configure?{h(urlencode({"module": module}))}">
                            <i class="fa-solid fa-sliders"></i> Module Settings
                        </a>"""
        signature_state = str(module_info.get("signature_state") or "").lower()
        if module_info.get("trusted"):
            signature_html = f'<span class="status-pill signed"><i class="fa-solid fa-circle-check"></i>{h(module_info.get("signature_label") or "Signed by CA")}</span>'
        elif signature_state == "untrusted":
            signature_html = f'<span class="status-pill error"><i class="fa-solid fa-circle-question"></i>{h(module_info.get("load_error") or "No installed CA trusts this module. Refer to the developer for information.")}</span>'
        else:
            signature_html = f'<span class="status-pill error"><i class="fa-solid fa-ban"></i>{h(module_info.get("load_error") or "This module is unsigned and cannot be verified")}</span>'
        status_html = f"""<div class="module-status">
                    {signature_html}
                    {f'<span class="status-pill disabled"><i class="fa-solid fa-toggle-off"></i> Disabled</span>' if can_load and not is_enabled else ''}
                    {f'<span class="status-pill"><i class="fa-solid fa-plug-circle-check"></i> Loaded</span>' if module_info.get("loaded") else ''}
                </div>"""
        description = f'<div class="module-meta">{h(module_info.get("description") or "")}</div>' if module_info.get("description") else ""
        version = f' - Version {h(module_info.get("version"))}' if module_info.get("version") else ""
        developer = h(module_info.get("developer") or module_info.get("author") or module_info.get("maintainer") or "Unknown Developer")
        controls = ""
        if can_load:
            controls = f"""{settings_link}
                    <form method="post">
                        <input type="hidden" name="action" value="toggle_module">
                        <input type="hidden" name="module" value="{h(module)}">
                        <button class="toggle-button {'enabled' if is_enabled else 'disabled'}" type="submit">
                            <i class="fa-solid {'fa-toggle-on' if is_enabled else 'fa-toggle-off'}"></i>
                            {'Disable' if is_enabled else 'Enable'}
                        </button>
                    </form>"""
        else:
            controls = '<span class="cannot-load">This module cannot be loaded</span>'
        cards += f"""<section class="module-card">
            <div class="module-head">
                <div>
                    <div class="module-title">{h(module_info.get("name") or module)}</div>
                    <div class="module-meta">{developer}{f' - {h(module_info.get("input_type"))}' if module_info.get("input_type") else ''}{version}</div>
                    {description}
                    {status_html}
                </div>
                <div class="module-controls">
                    {controls}
                </div>
            </div>
        </section>"""
    if not cards:
        cards = '<p class="muted">No endpoint modules found.</p>'
    upload_modal = f"""
<div id="upload-modal" class="modal-backdrop" onclick="modalBackdropClick(event)">
    <div class="modal-card">
        <div class="modal-header">
            <h2>Upload endpoint module</h2>
            <button class="modal-close" type="button" onclick="closeUploadModal()"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <form method="post" enctype="multipart/form-data">
            <div class="modal-body">
                <input type="hidden" name="action" value="upload_module">
                <input type="hidden" name="MAX_FILE_SIZE" value="{MAX_MODULE_UPLOAD_BYTES}">
                <div class="upload-box">
                    <i class="fa-solid fa-cloud-arrow-up" style="font-size:2em;"></i>
                    <div style="margin-top:10px;">Choose an endpoint module package</div>
                    <div class="muted" style="margin-top:6px;">Allowed: .opsepm. Limit: {h(format_bytes(MAX_MODULE_UPLOAD_BYTES))}.</div>
                    <input class="control" type="file" name="module_file" accept=".opsepm" required>
                </div>
            </div>
            <div class="modal-actions">
                <button class="button subtle" type="button" onclick="closeUploadModal()">Cancel</button>
                <button class="button" type="submit"><i class="fa-solid fa-upload"></i> Upload</button>
            </div>
        </form>
    </div>
</div>"""
    content = f"""    <div class="header-actions">
        <h1>Manage Endpoint Modules</h1>
        <div class="toolbar">
            <button class="button" type="button" onclick="openUploadModal()"><i class="fa-solid fa-upload"></i> Upload</button>
            <a class="back-link" href="/admin/manage-endpoints"><i class="fa-solid fa-arrow-left"></i> Back</a>
        </div>
    </div>
    {notices}
    {cards}
    {upload_modal}"""
    return legacy_page("Manage Endpoint Modules", legacy_user_context(user), "endpoints", MODULE_SETTINGS_STYLE, content, MODULE_SETTINGS_SCRIPT)
