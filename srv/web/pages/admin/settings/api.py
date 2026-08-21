from srv.web.app import *
from srv.web.pages.admin.settings.sip import is_port_in_use
from srv.web.pages.admin.settings.common import settings_page


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = ensure_certificate_schema(settings())
    if request.method == "POST":
        if demo_mode_enabled():
            return jsonify(status="error", message="Demo Mode is enabled.") if request.headers.get("X-Requested-With") == "XMLHttpRequest" else demo_mode_page("API Settings", legacy_user_context(user), "settings", "settings")
        http_enabled = "1" if request.form.get("api_http_enable") else "0"
        http_port = request.form.get("api_http_port", "8088").strip()
        https_enabled = "1" if request.form.get("api_https_enable") else "0"
        https_port = request.form.get("api_https_port", "8089").strip()
        certificate_id = request.form.get("api_https_certificate_id", data.get("api_https_certificate_id", "")).strip()
        http_to_https = "1" if http_enabled == "1" and https_enabled == "1" and request.form.get("api_http_to_https") else "0"
        hsts_enabled = "1" if https_enabled == "1" and request.form.get("api_hsts") else "0"
        errors = []

        def validate_port(value, label, enabled_value, old_value):
            try:
                numeric = int(value)
                if numeric < 1 or numeric > 65535:
                    errors.append(f"Invalid {label} port range.")
                elif enabled_value == "1" and str(value) != str(old_value) and is_port_in_use(value):
                    errors.append(f"Port {value} is already in use.")
            except ValueError:
                errors.append(f"Invalid {label} port range.")

        validate_port(http_port, "API HTTP", http_enabled, data.get("api_http_port", "8088"))
        validate_port(https_port, "API HTTPS", https_enabled, data.get("api_https_port", "8089"))
        if http_enabled == "1" and https_enabled == "1" and http_port == https_port:
            errors.append("API HTTP and HTTPS ports must be different.")

        enabled_ports = []
        if http_enabled == "1":
            enabled_ports.append((http_port, "API HTTP"))
        if https_enabled == "1":
            enabled_ports.append((https_port, "API HTTPS"))
        web_ports = []
        if data.get("webserver_enable", "1") == "1":
            web_ports.append((str(data.get("webserver_http_port", "80")), "Web HTTP"))
            if data.get("webserver_https_enable", "0") == "1":
                web_ports.append((str(data.get("webserver_https_port", "443")), "Web HTTPS"))
        for api_port, api_label in enabled_ports:
            for web_port, web_label in web_ports:
                if str(api_port) == str(web_port):
                    errors.append(f"{api_label} and {web_label} ports must be different.")

        if https_enabled == "1":
            certificate = certificate_record(certificate_id)
            if not certificate:
                errors.append("Select a certificate when API HTTPS is enabled.")
            else:
                try:
                    validate_tls_certificate(certificate["certificate_path"], certificate["private_key_path"])
                except ValueError as exc:
                    errors.append(str(exc))
        if errors:
            message = " ".join(dict.fromkeys(errors))
            return jsonify(status="error", message=message) if request.headers.get("X-Requested-With") == "XMLHttpRequest" else page("API Settings", h(message), "settings", user)

        save_setting("api_http_enable", http_enabled, "Enable REST API over HTTP (0/1)")
        save_setting("api_http_port", http_port or "8088", "REST API HTTP port")
        save_setting("api_https_enable", https_enabled, "Enable REST API over HTTPS (0/1)")
        save_setting("api_https_port", https_port or "8089", "REST API HTTPS port")
        save_setting("api_http_to_https", http_to_https, "Automatically redirect API HTTP requests to HTTPS (0/1)")
        save_setting("api_hsts", hsts_enabled, "Send HSTS headers over API HTTPS (0/1)")
        if https_enabled == "1" and certificate_id:
            set_certificate_for_service("api", certificate_id)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/api")

    ctx = legacy_user_context(user)
    http_checked = " checked" if data.get("api_http_enable", "0") == "1" else ""
    http_disabled = "" if http_checked else " disabled"
    https_checked = " checked" if data.get("api_https_enable", "0") == "1" else ""
    https_disabled = "" if https_checked else " disabled"
    redirect_checked = " checked" if data.get("api_http_to_https", "0") == "1" else ""
    hsts_checked = " checked" if data.get("api_hsts", "0") == "1" else ""
    certificate_options = '<option value="">Select a certificate</option>' + "".join(
        f'<option value="{int(record["id"])}"{" selected" if str(record["id"]) == str(data.get("api_https_certificate_id", "")) else ""}>{h(record.get("name"))}</option>'
        for record in certificate_records()
    )
    body = f"""
    <div id="api" class="tab-content active">
        <div class="info-card login-settings">
            <p>Configure HTTP and HTTPS listeners for the REST API.</p>
            <form id="apiSettingsForm">
                <div class="info-row" style="border-bottom:none; padding-bottom:0;">
                    <span class="info-label">Enable REST API over HTTP</span>
                    <span><label class="switch"><input type="checkbox" name="api_http_enable" id="apiToggle"{http_checked}><span class="slider"></span></label></span>
                </div>
                <div class="info-row" style="flex-direction:column; align-items:flex-start; gap:8px; margin-bottom:16px;">
                    <span class="info-label">HTTP Port</span>
                    <input type="number" name="api_http_port" id="apiPort" min="1" max="65535" value="{h(data.get("api_http_port", "8088") or "8088")}"{http_disabled}>
                    <span id="apiPortError" class="port-error-text">Please enter a valid port (1-65535).</span>
                </div>
                <div class="info-row" style="border-bottom:none;">
                    <span class="info-label">Enable HTTPS</span>
                    <span><label class="switch"><input type="checkbox" name="api_https_enable" id="apiHttpsToggle"{https_checked}><span class="slider"></span></label></span>
                </div>
                <div id="apiHttpsSettings">
                    <div class="info-row" style="flex-direction:column; align-items:flex-start; gap:8px; border-bottom:none;">
                        <span class="info-label">HTTPS Port</span>
                        <input type="number" name="api_https_port" id="apiHttpsPort" min="1" max="65535" value="{h(data.get("api_https_port", "8089") or "8089")}"{https_disabled}>
                        <span id="apiHttpsPortError" class="port-error-text">Please enter a valid port (1-65535).</span>
                    </div>
                    <div class="info-row" style="flex-direction:column; align-items:flex-start; gap:8px; border-bottom:none;">
                        <span class="info-label">Certificate</span>
                        <select name="api_https_certificate_id" id="apiHttpsCertificate"{https_disabled}>{certificate_options}</select>
                        <span class="info-description">Certificates are managed in the Certificates settings panel.</span>
                    </div>
                    <div class="info-row" style="border-bottom:none;">
                        <span class="info-label">Auto Upgrade HTTP to HTTPS</span>
                        <span><label class="switch"><input type="checkbox" name="api_http_to_https" id="apiHttpToHttpsToggle"{redirect_checked}{https_disabled}><span class="slider"></span></label></span>
                    </div>
                    <div class="info-row" style="border-bottom:none;">
                        <span class="info-label">Send HSTS Header</span>
                        <span><label class="switch"><input type="checkbox" name="api_hsts" id="apiHstsToggle"{hsts_checked}{https_disabled}><span class="slider"></span></label></span>
                    </div>
                </div>
                <input type="hidden" name="save_api_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
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
    const httpsToggle = document.getElementById('apiHttpsToggle');
    const httpsSettings = document.getElementById('apiHttpsSettings');
    const httpsPort = document.getElementById('apiHttpsPort');
    const httpsPortError = document.getElementById('apiHttpsPortError');
    const httpsCertificate = document.getElementById('apiHttpsCertificate');
    const redirectToggle = document.getElementById('apiHttpToHttpsToggle');
    const hstsToggle = document.getElementById('apiHstsToggle');
    function validatePort(input, error) {
        const value = Number(input.value);
        const valid = input.value !== '' && Number.isFinite(value) && value >= 1 && value <= 65535;
        input.classList.toggle('invalid-port', !valid);
        error.style.display = valid ? 'none' : 'block';
        return valid;
    }
    [apiPort, httpsPort].forEach((input, index) => input.addEventListener('input', function() {
        if (Number(this.value) > 65535) this.value = '65535';
        if (this.value.length > 5) this.value = this.value.slice(0, 5);
        validatePort(this, index === 0 ? apiPortError : httpsPortError);
    }));
    function syncHttp() {
        apiPort.disabled = !apiToggle.checked;
        if (apiToggle.checked) validatePort(apiPort, apiPortError);
        else { apiPort.classList.remove('invalid-port'); apiPortError.style.display = 'none'; }
        if (redirectToggle) redirectToggle.disabled = !apiToggle.checked || !httpsToggle.checked;
    }
    function syncHttps() {
        const enabled = httpsToggle.checked;
        httpsSettings.style.display = enabled ? '' : 'none';
        httpsPort.disabled = !enabled;
        httpsCertificate.disabled = !enabled;
        hstsToggle.disabled = !enabled;
        redirectToggle.disabled = !enabled || !apiToggle.checked;
        if (enabled) validatePort(httpsPort, httpsPortError);
        else { httpsPort.classList.remove('invalid-port'); httpsPortError.style.display = 'none'; redirectToggle.checked = false; hstsToggle.checked = false; }
    }
    apiToggle.addEventListener('change', syncHttp);
    httpsToggle.addEventListener('change', syncHttps);
    syncHttp();
    syncHttps();
    postSettings('apiSettingsForm','saveApiBtn','api-save-status','API settings saved.', false);
});
"""
    return settings_page("API Settings", ctx, "api", body, script)
