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
            return jsonify(status="error", message="Demo Mode is enabled.") if request.headers.get("X-Requested-With") == "XMLHttpRequest" else demo_mode_page("API Settings", legacy_user_context(user), "settings", "settings")
        enabled_value = "1" if request.form.get("api_http_enable") else "0"
        http_port = request.form.get("api_http_port", "8088").strip()
        errors = []
        try:
            numeric = int(http_port)
            if numeric < 1 or numeric > 65535:
                errors.append("Invalid port range.")
            elif enabled_value == "1" and str(http_port) != str(data.get("api_http_port", "8088")) and is_port_in_use(http_port):
                errors.append(f"Port {http_port} is already in use.")
            elif enabled_value == "1" and str(http_port) == str(data.get("webserver_http_port", "80")):
                errors.append("Web and API ports must be different.")
        except ValueError:
            errors.append("Invalid port range.")
        if errors:
            return jsonify(status="error", message=" ".join(errors)) if request.headers.get("X-Requested-With") == "XMLHttpRequest" else page("API Settings", h(" ".join(errors)), "settings", user)
        save_setting("api_http_enable", enabled_value, "Enable REST API over HTTP (0/1)")
        save_setting("api_http_port", http_port or "8088", "REST API HTTP port")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/api")
    ctx = legacy_user_context(user)
    checked = " checked" if data.get("api_http_enable", "0") == "1" else ""
    disabled = "" if checked else " disabled"
    body = f"""
    <div id="api" class="tab-content active">
        <div class="info-card login-settings">
            <p>Enable the REST API over HTTP and choose the port used by the API service.</p>
            <form id="apiSettingsForm">
                <div class="info-row" style="border-bottom:none; padding-bottom:0;">
                    <span class="info-label">Enable REST API over HTTP</span>
                    <span><label class="switch"><input type="checkbox" name="api_http_enable" id="apiToggle"{checked}><span class="slider"></span></label></span>
                </div>
                <div class="info-row stacked" style="margin-bottom:16px;">
                    <span class="info-label">Port</span>
                    <input type="number" name="api_http_port" id="apiPort" min="1" max="65535" value="{h(data.get("api_http_port", "8088") or "8088")}"{disabled}>
                    <span id="apiPortError" class="port-error-text">Please enter a valid port (1-65535).</span>
                </div>
                <input type="hidden" name="save_api_settings" value="1">
                <div class="settings-actions">
                    <button type="button" id="saveApiBtn">Save Settings</button>
                    <span id="api-save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>"""
    script = r"""
document.addEventListener('DOMContentLoaded', function() {
    const apiToggle = document.getElementById('apiToggle');
    const apiPort = document.getElementById('apiPort');
    const apiPortError = document.getElementById('apiPortError');
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
    if (apiPort) {
        apiPort.addEventListener('input', function() {
            if (this.value > 65535) this.value = 65535;
            if (this.value.length > 5) this.value = this.value.slice(0, 5);
            validatePortInput(this, apiPortError);
        });
    }
    if (apiToggle) {
        apiToggle.addEventListener('change', function() {
            apiPort.disabled = !this.checked;
            if (this.checked) validatePortInput(apiPort, apiPortError);
            else {
                apiPort.classList.remove('invalid-port');
                apiPortError.style.display = 'none';
            }
        });
    }
    postSettings('apiSettingsForm','saveApiBtn','api-save-status','API settings saved.', false);
});
"""
    return settings_page("API Settings", ctx, "api", body, script)
