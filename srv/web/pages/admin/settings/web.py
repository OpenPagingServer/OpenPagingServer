from srv.web.app import *
from srv.web.pages.admin.settings.sip import is_port_in_use
from srv.web.pages.admin.settings.common import settings_page


def normalize_toggle(value):
    return "1" if value else "0"


def validate_absolute_server_path(value):
    return str(value or "").startswith("/")


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    if request.method == "POST":
        if demo_mode_enabled():
            return jsonify(status="error", message="Demo Mode is enabled.") if request.headers.get("X-Requested-With") == "XMLHttpRequest" else demo_mode_page("Web Settings", legacy_user_context(user), "settings", "settings")
        http_port = request.form.get("webserver_http_port", "80").strip()
        https_enabled = normalize_toggle(request.form.get("webserver_https_enable"))
        https_port = request.form.get("webserver_https_port", data.get("webserver_https_port", "443")).strip()
        https_privkey = request.form.get("webserver_https_privkey", data.get("webserver_https_privkey", "")).strip()
        https_cert = request.form.get("webserver_https_cert", data.get("webserver_https_cert", "")).strip()
        http_to_https = normalize_toggle(request.form.get("webserver_http_to_https")) if https_enabled == "1" else "0"
        hsts_enabled = normalize_toggle(request.form.get("webserver_hsts")) if https_enabled == "1" else "0"
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
        if https_enabled == "1":
            try:
                https_numeric = int(https_port)
                if https_numeric < 1 or https_numeric > 65535:
                    errors.append("Invalid HTTPS port range.")
                elif https_port == http_port:
                    errors.append("HTTP and HTTPS ports must be different.")
                elif str(https_port) != str(data.get("webserver_https_port", "443")) and is_port_in_use(https_port):
                    errors.append(f"Port {https_port} is already in use.")
                elif data.get("api_http_enable", "0") == "1" and str(https_port) == str(data.get("api_http_port", "8088")):
                    errors.append("HTTPS and API ports must be different.")
            except ValueError:
                errors.append("Invalid HTTPS port range.")
            if not https_privkey:
                errors.append("Private key path is required when HTTPS is enabled.")
            elif not validate_absolute_server_path(https_privkey):
                errors.append("Private key path must start with /.")
            if not https_cert:
                errors.append("Certificate path is required when HTTPS is enabled.")
            elif not validate_absolute_server_path(https_cert):
                errors.append("Certificate path must start with /.")
        if errors:
            return jsonify(status="error", message=" ".join(errors)) if request.headers.get("X-Requested-With") == "XMLHttpRequest" else page("Web Settings", h(" ".join(errors)), "settings", user)
        save_setting("webserver_enable", "1", "Enable access to Open Paging Server via a web browser (0/1)")
        save_setting("webserver_http_port", http_port, "HTTP Server Port (Default: 80)")
        save_setting("webserver_https_enable", https_enabled, "HTTPs Enable (0/1)")
        save_setting("webserver_https_port", https_port or "443", "HTTPs Server Port (Default: 443)")
        save_setting("webserver_https_privkey", https_privkey, "HTTPS private key path on the server. Must start with /")
        save_setting("webserver_https_cert", https_cert, "HTTPS certificate path on the server. Must start with /")
        save_setting("webserver_http_to_https", http_to_https, "Automatically redirect HTTP requests to HTTPS (0/1)")
        save_setting("webserver_hsts", hsts_enabled, "Send HSTS headers over HTTPS (0/1)")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/web")
    ctx = legacy_user_context(user)
    https_checked = " checked" if data.get("webserver_https_enable", "0") == "1" else ""
    https_field_disabled = "" if https_checked else " disabled"
    http_to_https_checked = " checked" if data.get("webserver_http_to_https", "0") == "1" else ""
    hsts_checked = " checked" if data.get("webserver_hsts", "0") == "1" else ""
    body = f"""
    <div id="web" class="tab-content active">
        <div class="info-card login-settings">
            <p>Configure the web server</p>
            <form id="webSettingsForm">
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; margin-bottom:16px;">
                    <span class="info-label">HTTP Port</span>
                    <input type="number" name="webserver_http_port" id="webHttpPort" min="1" max="65535" value="{h(data.get("webserver_http_port", "80") or "80")}">
                    <span id="webHttpPortError" class="port-error-text">Please enter a valid port (1-65535).</span>
                </div>
                <div class="info-row" style="border-bottom:none;">
                    <span class="info-label">
                        Enable HTTPS
                    </span>
                    <span><label class="switch"><input type="checkbox" name="webserver_https_enable" id="webHttpsToggle"{https_checked}><span class="slider"></span></label></span>
                </div>
                <div id="webHttpsSettings">
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; margin-bottom:16px; border-top:none; border-bottom:none;">
                    <span class="info-label">HTTPS Port</span>
                    <input type="number" name="webserver_https_port" id="webHttpsPort" min="1" max="65535" value="{h(data.get("webserver_https_port", "443") or "443")}"{https_field_disabled}>
                    <span id="webHttpsPortError" class="port-error-text">Please enter a valid port (1-65535).</span>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom:none;">
                    <span class="info-label">Private Key Path</span>
                    <input type="text" name="webserver_https_privkey" id="webHttpsPrivkey" value="{h(data.get("webserver_https_privkey", ""))}"{https_field_disabled}>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom:none;">
                    <span class="info-label">Certificate Path</span>
                    <input type="text" name="webserver_https_cert" id="webHttpsCert" value="{h(data.get("webserver_https_cert", ""))}"{https_field_disabled}>
                </div>
                <div class="info-row" style="border-bottom:none;">
                    <span class="info-label">Auto Upgrade HTTP to HTTPS</span>
                    <span><label class="switch"><input type="checkbox" name="webserver_http_to_https" id="webHttpToHttpsToggle"{http_to_https_checked}{https_field_disabled}><span class="slider"></span></label></span>
                </div>
                <div class="info-row" style="border-bottom:none;">
                    <span class="info-label">Send HSTS Header</span>
                    <span><label class="switch"><input type="checkbox" name="webserver_hsts" id="webHstsToggle"{hsts_checked}{https_field_disabled}><span class="slider"></span></label></span>
                </div>
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
    const httpsToggle = document.getElementById('webHttpsToggle');
    const portInput = document.getElementById('webHttpPort');
    const portError = document.getElementById('webHttpPortError');
    const httpsSettings = document.getElementById('webHttpsSettings');
    const httpsPortInput = document.getElementById('webHttpsPort');
    const httpsPortError = document.getElementById('webHttpsPortError');
    const httpsPrivkey = document.getElementById('webHttpsPrivkey');
    const httpsCert = document.getElementById('webHttpsCert');
    const httpToHttpsToggle = document.getElementById('webHttpToHttpsToggle');
    const hstsToggle = document.getElementById('webHstsToggle');
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
    if (httpsPortInput) {
        httpsPortInput.addEventListener('input', function() {
            if (this.value > 65535) this.value = 65535;
            if (this.value.length > 5) this.value = this.value.slice(0, 5);
            validatePortInput(this, httpsPortError);
        });
    }
    function syncHttpsFields() {
        if (!httpsToggle) return;
        const enabled = httpsToggle.checked;
        if (httpsSettings) httpsSettings.style.display = enabled ? '' : 'none';
        if (httpsPortInput) {
            httpsPortInput.disabled = !enabled;
            if (enabled) validatePortInput(httpsPortInput, httpsPortError);
            else {
                httpsPortInput.classList.remove('invalid-port');
                httpsPortError.style.display = 'none';
            }
        }
        if (httpsPrivkey) httpsPrivkey.disabled = !enabled;
        if (httpsCert) httpsCert.disabled = !enabled;
        if (httpToHttpsToggle) {
            httpToHttpsToggle.disabled = !enabled;
            if (!enabled) httpToHttpsToggle.checked = false;
        }
        if (hstsToggle) {
            hstsToggle.disabled = !enabled;
            if (!enabled) hstsToggle.checked = false;
        }
    }
    if (httpsToggle) {
        httpsToggle.addEventListener('change', syncHttpsFields);
        syncHttpsFields();
    }
    postSettings('webSettingsForm','saveWebBtn','web-save-status','Web settings saved.', false);
});
"""
    return settings_page("Web Settings", ctx, "web", body, script)
