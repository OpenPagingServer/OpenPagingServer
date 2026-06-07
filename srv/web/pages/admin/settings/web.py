from srv.web.app import *
from srv.web.pages.admin.settings.sip import is_port_in_use
from srv.web.pages.admin.settings.common import settings_page


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    if request.method == "POST":
        if demo_mode_enabled():
            return jsonify(status="error", message="Demo Mode is enabled.") if request.headers.get("X-Requested-With") == "XMLHttpRequest" else demo_mode_page("Web Settings", legacy_user_context(user), "settings", "settings")
        http_port = request.form.get("webserver_http_port", "80").strip()
        errors = []
        try:
            numeric = int(http_port)
            if numeric < 1 or numeric > 65535:
                errors.append("Invalid port range.")
            elif str(http_port) != str(data.get("webserver_http_port", "80")) and is_port_in_use(http_port):
                errors.append(f"Port {http_port} is already in use.")
            elif data.get("api_http_enable", "0") == "1" and str(http_port) == str(data.get("api_http_port", "8088")):
                errors.append("Web and API ports must be different.")
        except ValueError:
            errors.append("Invalid port range.")
        if errors:
            return jsonify(status="error", message=" ".join(errors)) if request.headers.get("X-Requested-With") == "XMLHttpRequest" else page("Web Settings", h(" ".join(errors)), "settings", user)
        save_setting("webserver_enable", "1", "Enable access to Open Paging Server via a web browser (0/1)")
        save_setting("webserver_http_port", http_port, "HTTP Server Port (Default: 80)")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/web")
    ctx = legacy_user_context(user)
    body = f"""
    <div id="web" class="tab-content active">
        <div class="info-card login-settings">
            <p>Change the HTTP port used by the web interface.</p>
            <form id="webSettingsForm">
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; margin-bottom:16px;">
                    <span class="info-label">HTTP Port</span>
                    <input type="number" name="webserver_http_port" id="webHttpPort" min="1" max="65535" value="{h(data.get("webserver_http_port", "80") or "80")}">
                    <span id="webHttpPortError" class="port-error-text">Please enter a valid port (1-65535).</span>
                </div>
                <input type="hidden" name="save_web_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveWebBtn">Save Settings</button>
                    <span id="web-save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>"""
    script = r"""
document.addEventListener('DOMContentLoaded', function() {
    const portInput = document.getElementById('webHttpPort');
    const portError = document.getElementById('webHttpPortError');
    function validatePortInput(input, errorElement) {
        let val = parseInt(input.value);
        if (input.value === "" || isNaN(val) || val < 1 || val > 65535) {
            input.classList.add('invalid-port');
            errorElement.style.display = 'block';
            return false;
        }
        input.classList.remove('invalid-port');
        errorElement.style.display = 'none';
        return true;
    }
    if (portInput) {
        portInput.addEventListener('input', function() {
            if (this.value > 65535) this.value = 65535;
            if (this.value.length > 5) this.value = this.value.slice(0, 5);
            validatePortInput(this, portError);
        });
    }
    postSettings('webSettingsForm','saveWebBtn','web-save-status','Web settings saved.', false);
});
"""
    return settings_page("Web Settings", ctx, "web", body, script)
