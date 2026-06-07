
from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page


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
        save_setting("product_name", request.form.get("product_name", "Open Paging Server"), "Name of this server.")
        save_setting("use_logo_in_sidebar", "1" if request.form.get("use_logo_in_sidebar") else "0", "Use a logo in the sidebar, if disabled the product name will show")
        save_setting("sidebar_logo_light", request.form.get("sidebar_logo_light", "/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png").strip(), "Light mode logo for the sidebar")
        save_setting("sidebar_logo_dark", request.form.get("sidebar_logo_dark", "/assets/OPENPAGINGSERVER-768x576-DARKMODE.png").strip(), "Dark mode logo for the sidebar")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/branding")
    ctx = legacy_user_context(user)
    checked = " checked" if truthy(data.get("use_logo_in_sidebar", "1")) else ""
    body = f"""
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
                    <label style="display:flex; gap:8px; align-items:center; margin-bottom:12px;">
                        <input type="checkbox" name="use_logo_in_sidebar" value="1"{checked} style="width:auto;">
                        <span>Use logo in sidebar</span>
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
                <input type="hidden" name="save_branding_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveBrandingBtn">Save Branding Settings</button>
                    <span id="branding-save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>"""
    script = "document.addEventListener('DOMContentLoaded', function(){ postSettings('brandingSettingsForm','saveBrandingBtn','branding-save-status','Branding saved.', true); });"
    return settings_page("Branding", ctx, "branding", body, script)
