
from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    if request.method == "POST":
        save_setting("login_banner_enabled", "1" if request.form.get("login_banner_enabled") else "0", "Enable login banner")
        save_setting("login_banner_title", request.form.get("login_banner_title", ""), "Login banner title")
        save_setting("login_banner_message", request.form.get("login_banner_message", ""), "Login banner message")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/login")
    ctx = legacy_user_context(user)
    enabled = data.get("login_banner_enabled", "0") == "1"
    checked = " checked" if enabled else ""
    disabled = "" if enabled else " disabled"
    body = f"""
    <div id="login" class="tab-content active">
        <div class="info-card login-settings">
            <form id="loginSettingsForm">
                <div class="info-row">
                    <span class="info-label">Enable Banner</span>
                    <span><label class="switch"><input type="checkbox" name="login_banner_enabled" id="bannerToggle"{checked}><span class="slider"></span></label></span>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                    <span class="info-label">Title</span>
                    <input type="text" name="login_banner_title" id="bannerTitle" value="{h(data.get("login_banner_title", ""))}"{disabled}>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                    <span class="info-label">Message</span>
                    <textarea name="login_banner_message" id="bannerMessage"{disabled}>{h(data.get("login_banner_message", ""))}</textarea>
                </div>
                <input type="hidden" name="save_login_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveLoginBtn">Save Settings</button>
                    <span id="save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>"""
    script = r"""
document.addEventListener('DOMContentLoaded', function() {
    const bannerToggle = document.getElementById('bannerToggle');
    const bannerTitle = document.getElementById('bannerTitle');
    const bannerMessage = document.getElementById('bannerMessage');
    if (bannerToggle) {
        bannerToggle.addEventListener('change', function() {
            bannerTitle.disabled = !this.checked;
            bannerMessage.disabled = !this.checked;
        });
    }
    postSettings('loginSettingsForm','saveLoginBtn','save-status','Settings saved successfully.', false);
});
"""
    return settings_page("Login Settings", ctx, "login", body, script)
