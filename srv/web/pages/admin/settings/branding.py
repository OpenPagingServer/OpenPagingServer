
from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page


def _login_background_value(value):
    if not str(value or "").strip():
        return ""
    path = login_background_asset_path(value)
    if path is None:
        raise ValueError("Select a supported image from the assets directory.")
    return path.name


def _login_background_options(selected):
    options = []
    for candidate in iter_asset_files():
        path = login_background_asset_path(candidate.name)
        supported = path is not None
        name = candidate.name
        suffix = candidate.suffix.lower()
        raw_url = "/assets/?raw=" + urlencode({"": name})[1:] if supported else ""
        if supported:
            preview = f'<img src="{h(raw_url)}" alt="{h(name)}">'
            meta = f"{suffix.lstrip('.').upper()} image"
            status = "Selectable"
        elif suffix in {".wav", ".mp3", ".ogg"}:
            preview = '<i class="fa-solid fa-music"></i>'
            meta = "Audio asset"
            status = "Unsupported format"
        elif suffix == ".txt":
            preview = '<i class="fa-solid fa-file-lines"></i>'
            meta = "Text asset"
            status = "Unsupported format"
        else:
            preview = '<i class="fa-solid fa-file"></i>'
            meta = "Unsupported asset"
            status = "Unsupported format"
        classes = "message-icon-asset-card" + (" selected" if supported and name.lower() == selected.lower() else "") + ("" if supported else " unsupported")
        options.append(
            f'''<button type="button" class="{classes}" data-name="{h(name)}" data-url="{h(raw_url)}" data-supported="{'1' if supported else '0'}" onclick="chooseLoginBackground(this)">
                <div class="message-icon-asset-preview">{preview}</div>
                <div class="message-icon-asset-info">
                    <div class="message-icon-asset-name" title="{h(name)}">{h(name)}</div>
                    <div class="message-icon-asset-meta">{h(meta)}</div>
                    <div class="message-icon-asset-status">{h(status)}</div>
                </div>
            </button>'''
        )
    return "".join(options) or '<div class="message-icon-picker-empty">No assets are stored on the server yet.</div>'


def has_branding_permission(user):
    row = query_one("SELECT role, adminperm FROM users WHERE id=%s LIMIT 1", (user.get("id"),)) or {}
    perms = {part.strip() for part in str(row.get("adminperm") or "").split(",")}
    return "all" in perms or "settings-branding" in perms or row.get("role") in {"admin", "tempadmin"}


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    if not has_branding_permission(user):
        abort(403)
    data = settings()
    if request.method == "POST":
        if demo_mode_enabled():
            return jsonify(status="error", message="Demo Mode is enabled.") if request.headers.get("X-Requested-With") == "XMLHttpRequest" else demo_mode_page("Branding", legacy_user_context(user), "settings", "settings")
        try:
            login_background = _login_background_value(request.form.get("login_background_image", ""))
        except ValueError as exc:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="error", message=str(exc)), 400
            abort(400, str(exc))
        save_setting("product_name", request.form.get("product_name", "Open Paging Server"), "Name of this server.")
        save_setting("use_logo_in_sidebar", "1" if request.form.get("use_logo_in_sidebar") else "0", "Use a logo in the sidebar, if disabled the product name will show")
        save_setting("sidebar_logo_light", request.form.get("sidebar_logo_light", "/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png").strip(), "Light mode logo for the sidebar")
        save_setting("sidebar_logo_dark", request.form.get("sidebar_logo_dark", "/assets/OPENPAGINGSERVER-768x576-DARKMODE.png").strip(), "Dark mode logo for the sidebar")
        save_setting("login_background_image", login_background, "Background image shown on the login page")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/branding")
    ctx = legacy_user_context(user)
    checked = " checked" if truthy(data.get("use_logo_in_sidebar", "1")) else ""
    try:
        selected_background = _login_background_value(data.get("login_background_image", ""))
    except ValueError:
        selected_background = ""
    selected_background_url = "/assets/?raw=" + urlencode({"": selected_background})[1:] if selected_background else ""
    background_options = _login_background_options(selected_background)
    body = f"""
    <style>
    .message-icon-selection{{display:flex;flex-direction:column;align-items:flex-start;gap:12px}}
    .message-icon-summary{{display:flex;align-items:center;gap:14px;min-width:0;width:100%}}
    .message-icon-preview{{width:72px;height:72px;border-radius:14px;border:1px solid #e0e0e0;background:#f8fafd;display:flex;align-items:center;justify-content:center;overflow:hidden;color:#5f6368;font-size:1.4em;flex:0 0 auto}}
    .message-icon-preview img{{width:100%;height:100%;object-fit:cover}}
    .message-icon-text{{min-width:0}}
    .message-icon-name{{font-weight:500;font-size:1em;word-break:break-word}}
    .message-icon-meta{{font-size:.9em;color:#666;margin-top:4px}}
    .message-icon-actions{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
    .login-settings button.message-icon-clear{{border:1px solid #dadce0;background:#fff;color:#374151;border-radius:8px;padding:9px 12px}}
    .message-icon-picker-backdrop{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:1450}}
    .message-icon-picker-backdrop.open{{display:block}}
    .message-icon-picker-modal{{display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:min(980px,calc(100vw - 32px));max-height:calc(100vh - 40px);overflow:hidden;background:#fff;border-radius:18px;box-shadow:0 18px 50px rgba(0,0,0,.28);z-index:1500}}
    .message-icon-picker-modal.open{{display:flex;flex-direction:column}}
    .message-icon-picker-header{{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 20px;border-bottom:1px solid #eee}}
    .message-icon-picker-header h2{{margin:0;font-size:1.2em;font-weight:500}}
    .login-settings button.message-icon-picker-close{{border:none;background:transparent;color:#666;font-size:1.4em;line-height:1;padding:4px 6px}}
    .message-icon-picker-body{{padding:18px 20px 20px;overflow-y:auto}}
    .message-icon-picker-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px}}
    .login-settings button.message-icon-asset-card{{border:1px solid #dadce0;border-radius:16px;background:#fff;padding:0;cursor:pointer;text-align:left;color:inherit;overflow:hidden;box-shadow:0 1px 2px rgba(60,64,67,.08);display:block;width:100%;appearance:none}}
    .login-settings button.message-icon-asset-card:hover{{background:#fff;border-color:#b6c8e1;box-shadow:0 4px 10px rgba(60,64,67,.12)}}
    .login-settings button.message-icon-asset-card.selected{{border-color:var(--ops-accent);box-shadow:0 0 0 2px rgba(200,101,76,.18)}}
    .message-icon-asset-card.unsupported{{opacity:.48}}
    .login-settings button.message-icon-asset-card.unsupported:hover{{background:#fff;border-color:#dadce0;box-shadow:0 1px 2px rgba(60,64,67,.08)}}
    .message-icon-asset-preview{{height:132px;background:#f1f3f4;display:flex;align-items:center;justify-content:center;overflow:hidden;color:#5f6368;font-size:2em}}
    .message-icon-asset-preview img{{width:100%;height:100%;object-fit:cover}}
    .message-icon-asset-info{{padding:12px 14px 14px}}
    .message-icon-asset-name{{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .message-icon-asset-meta{{font-size:.84em;color:#666;margin-top:4px}}
    .message-icon-asset-status{{font-size:.83em;margin-top:8px;color:var(--ops-accent)}}
    .message-icon-asset-card.unsupported .message-icon-asset-status{{color:#a50e0e}}
    .message-icon-picker-empty{{grid-column:1/-1;padding:28px;border:1px dashed #dadce0;border-radius:16px;text-align:center;color:#5f6368;background:#f8fafd}}
    @media(max-width:767px){{.message-icon-picker-modal{{width:calc(100vw - 20px);max-height:calc(100vh - 20px)}}.message-icon-picker-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
    @media(prefers-color-scheme:dark){{.message-icon-preview{{background:#252525;border-color:#333;color:#bbb}}.message-icon-meta{{color:#aaa}}.login-settings button.message-icon-clear{{background:#252525;border-color:#444;color:#e5e7eb}}.message-icon-picker-modal{{background:#1e1e1e}}.message-icon-picker-header{{border-bottom-color:#333}}.login-settings button.message-icon-picker-close{{color:#aaa}}.login-settings button.message-icon-asset-card{{background:#252525;border-color:#3a3a3a;box-shadow:none}}.login-settings button.message-icon-asset-card:hover{{background:#252525;border-color:#4a4a4a;box-shadow:none}}.login-settings button.message-icon-asset-card.selected{{border-color:var(--ops-accent);box-shadow:0 0 0 2px rgba(200,101,76,.22)}}.message-icon-asset-preview{{background:#2a2a2a;color:#bbb}}.message-icon-asset-meta{{color:#aaa}}.message-icon-asset-card.unsupported .message-icon-asset-status{{color:#ef9a9a}}.login-settings button.message-icon-asset-card.unsupported:hover{{background:#252525;border-color:#3a3a3a;box-shadow:none}}.message-icon-picker-empty{{background:#202020;border-color:#333;color:#bbb}}}}
    </style>
    <div class="tab-content active">
        <div class="info-card login-settings">
            <form id="brandingSettingsForm">
                <div style="margin-bottom:16px;">
                    <h4>Product Name</h4>
                    <p>This displays throughout the various user interfaces of Open Paging Server. You can set a name to relfect your facility.</p>
                    <input type="text" name="product_name" value="{h(data.get("product_name") or "Open Paging Server")}">
                </div>
                <div style="margin-bottom:16px;">
                    <h4>Sidebar Logo</h4>
                    <p>When enabled, the sidebar uses the configured logo paths instead of the product name.</p>
                    <label class="md-checkbox-container" style="margin-bottom:12px;">
                        <input type="checkbox" name="use_logo_in_sidebar" value="1"{checked}>
                        <span class="md-checkmark"></span>
                        <span class="md-checkbox-text">Use logo in sidebar</span>
                    </label>
                    <div style="display:grid; gap:12px;">
                        <div>
                            <h4 style="font-size:1em;">Light Mode Sidebar Logo</h4>
                            <input type="text" name="sidebar_logo_light" value="{h(data.get("sidebar_logo_light") or "/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png")}">
                        </div>
                        <div>
                            <h4 style="font-size:1em;">Dark Mode Sidebar Logo</h4>
                            <input type="text" name="sidebar_logo_dark" value="{h(data.get("sidebar_logo_dark") or "/assets/OPENPAGINGSERVER-768x576-DARKMODE.png")}">
                        </div>
                    </div>
                </div>
                <div style="margin-bottom:16px;">
                    <h4>Login background image</h4>
                    <p>Select an image stored in the assets directory to use as the login page background.</p>
                    <input type="hidden" name="login_background_image" id="loginBackgroundValue" value="{h(selected_background)}">
                    <div class="message-icon-selection">
                        <div class="message-icon-summary">
                            <div class="message-icon-preview" id="loginBackgroundPreview">{f'<img src="{h(selected_background_url)}" alt="">' if selected_background else '<i class="fa-solid fa-image"></i>'}</div>
                            <div class="message-icon-text">
                                <div class="message-icon-name" id="loginBackgroundName">{h(selected_background or "No background selected")}</div>
                                <div class="message-icon-meta" id="loginBackgroundMeta">{h((Path(selected_background).suffix.lstrip('.').upper() + " image") if selected_background else "")}</div>
                            </div>
                        </div>
                        <div class="message-icon-actions">
                            <button type="button" onclick="openLoginBackgroundPicker()">Select Image</button>
                            <button type="button" class="message-icon-clear" id="loginBackgroundClear" onclick="clearLoginBackground()"{' disabled' if not selected_background else ''}>Clear</button>
                        </div>
                    </div>
                    <div class="message-icon-picker-backdrop" id="loginBackgroundBackdrop" onclick="closeLoginBackgroundPicker()"></div>
                    <div class="message-icon-picker-modal" id="loginBackgroundModal" role="dialog" aria-modal="true" aria-labelledby="loginBackgroundTitle">
                        <div class="message-icon-picker-header">
                            <h2 id="loginBackgroundTitle">Select Login Background Image</h2>
                            <button type="button" class="message-icon-picker-close" onclick="closeLoginBackgroundPicker()" aria-label="Close">&times;</button>
                        </div>
                        <div class="message-icon-picker-body"><div class="message-icon-picker-grid">{background_options}</div></div>
                    </div>
                </div>
                <input type="hidden" name="save_branding_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveBrandingBtn">Save Branding Settings</button>
                    <span id="branding-save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>"""
    script = r"""
function openLoginBackgroundPicker(){document.getElementById('loginBackgroundModal').classList.add('open');document.getElementById('loginBackgroundBackdrop').classList.add('open');}
function closeLoginBackgroundPicker(){document.getElementById('loginBackgroundModal').classList.remove('open');document.getElementById('loginBackgroundBackdrop').classList.remove('open');}
function chooseLoginBackground(button){
    if(button.getAttribute('data-supported')!=='1')return;
    const name=button.getAttribute('data-name')||'';
    const url=button.getAttribute('data-url')||'';
    document.getElementById('loginBackgroundValue').value=name;
    document.getElementById('loginBackgroundName').textContent=name;
    document.getElementById('loginBackgroundMeta').textContent=name.split('.').pop().toUpperCase()+' image';
    document.getElementById('loginBackgroundPreview').innerHTML='<img src="'+url+'" alt="">';
    document.getElementById('loginBackgroundClear').disabled=false;
    document.querySelectorAll('.message-icon-asset-card').forEach(function(card){card.classList.toggle('selected',card===button);});
    closeLoginBackgroundPicker();
}
function clearLoginBackground(){
    document.getElementById('loginBackgroundValue').value='';
    document.getElementById('loginBackgroundName').textContent='No background selected';
    document.getElementById('loginBackgroundMeta').textContent='';
    document.getElementById('loginBackgroundPreview').innerHTML='<i class="fa-solid fa-image"></i>';
    document.getElementById('loginBackgroundClear').disabled=true;
    document.querySelectorAll('.message-icon-asset-card.selected').forEach(function(card){card.classList.remove('selected');});
}
document.addEventListener('keydown',function(event){if(event.key==='Escape')closeLoginBackgroundPicker();});
document.addEventListener('DOMContentLoaded',function(){postSettings('brandingSettingsForm','saveBrandingBtn','branding-save-status','Branding saved.',true);});
"""
    return settings_page("Branding", ctx, "branding", body, script)
