from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    if request.method == "POST":
        if demo_mode_enabled():
            return jsonify(status="error", message="Demo Mode is enabled.") if request.headers.get("X-Requested-With") == "XMLHttpRequest" else demo_mode_page("General Settings", legacy_user_context(user), "settings", "settings")
        show_docs = "1" if request.form.get("show_online_docs") else "0"
        analytics = "1" if request.form.get("analytics") else "0"
        save_setting("show_online_docs", show_docs, "Show GUI links to docs.openpagingserver.org (0/1)")
        save_setting("analytics", analytics, "To help the Open Paging Server project improve, you can opt-in to share optional analytics.")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/general")
    ctx = legacy_user_context(user)
    show_docs_checked = " checked" if data.get("show_online_docs", "1") == "1" else ""
    analytics_checked = " checked" if data.get("analytics", "0") == "1" else ""
    body = f"""
    <div id="general" class="tab-content active">
        <div class="info-card login-settings">
            <form id="generalSettingsForm">
                <div class="info-row">
                    <span class="info-label">Show links to online documentation (docs.openpagingserver.org)</span>
                    <span><label class="switch"><input type="checkbox" name="show_online_docs" id="docsToggle"{show_docs_checked}><span class="slider"></span></label></span>
                </div>
                <div class="info-row">
                    <span class="info-label">
                        Send optional analytics to the Open Paging Server project
                        <span class="info-description">To help the Open Paging Server project improve, you can opt-in to share optional analytics. Analytics contain mainly anonymous data such as your operating system, software versions, anonymized crash logs, etc. And may include your public IP address. <a href="https://www.openpagingserver.org/privacypolicy/analytics" target="_blank" rel="noopener">Privacy Policy</a></span>
                    </span>
                    <span><label class="switch"><input type="checkbox" name="analytics" id="analyticsToggle"{analytics_checked}><span class="slider"></span></label></span>
                </div>
                <input type="hidden" name="save_general_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveGeneralBtn">Save Settings</button>
                    <span id="general-save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>"""
    script = "document.addEventListener('DOMContentLoaded', function(){ postSettings('generalSettingsForm','saveGeneralBtn','general-save-status','General settings saved.', false); });"
    return settings_page("General Settings", ctx, "general", body, script)
