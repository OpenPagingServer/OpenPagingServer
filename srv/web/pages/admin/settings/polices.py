from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    if request.method == "POST":
        if demo_mode_enabled():
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="error", message="Demo Mode is enabled.")
            return demo_mode_page("Polices", legacy_user_context(user), "settings", "settings")
        allow_user_custom_css = "1" if request.form.get("allow_user_custom_css") else "0"
        save_setting(
            "allow_user_custom_css",
            allow_user_custom_css,
            "Allow users to set their own custom CSS (0/1)",
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/polices")

    ctx = legacy_user_context(user)
    checked = " checked" if data.get("allow_user_custom_css", "1") == "1" else ""
    body = f"""
    <div id="polices" class="tab-content active">
        <div class="info-card login-settings">
            <form id="policesSettingsForm">
                <div class="info-row">
                    <span class="info-label">Allow users to set their own custom CSS</span>
                    <span><label class="switch"><input type="checkbox" name="allow_user_custom_css" id="allowUserCustomCssToggle"{checked}><span class="slider"></span></label></span>
                </div>
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="savePolicesBtn">Save Settings</button>
                    <span id="polices-save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>"""
    script = """
document.addEventListener('DOMContentLoaded', function(){
  postSettings('policesSettingsForm','savePolicesBtn','polices-save-status','Policy settings saved.', false);
});
"""
    return settings_page("Polices", ctx, "polices", body, script)
