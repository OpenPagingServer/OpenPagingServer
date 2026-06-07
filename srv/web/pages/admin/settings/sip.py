from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page


def is_port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.1):
            return True
    except OSError:
        return False


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    if request.method == "POST":
        if demo_mode_enabled():
            return jsonify(status="error", message="Demo Mode is enabled.") if request.headers.get("X-Requested-With") == "XMLHttpRequest" else demo_mode_page("SIP Settings", legacy_user_context(user), "settings", "settings")
        udp_enabled = "1" if request.form.get("enable_insecure_sip") else "0"
        udp_port = request.form.get("insecure_sip_port", "5060")
        tls_enabled = data.get("enable_secure_sip", "0")
        errors = []
        try:
            numeric = int(udp_port)
            if numeric < 1 or numeric > 65535:
                errors.append("Invalid port range.")
            elif udp_enabled == "1" and str(udp_port) != str(data.get("insecure_sip_port", "5060")) and is_port_in_use(udp_port):
                errors.append(f"Port {udp_port} is already in use.")
        except ValueError:
            errors.append("Invalid port range.")
        if errors:
            return jsonify(status="error", message=" ".join(errors)) if request.headers.get("X-Requested-With") == "XMLHttpRequest" else page("SIP Settings", h(" ".join(errors)), "settings", user)
        save_setting("sip", "1" if (udp_enabled == "1" or tls_enabled == "1") else "0", "Enable SIP")
        save_setting("enable_insecure_sip", udp_enabled, "Enable SIP over UDP/TCP")
        save_setting("insecure_sip_port", udp_port, "SIP UDP/TCP Port")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/sip")
    ctx = legacy_user_context(user)
    udp_checked = " checked" if data.get("enable_insecure_sip", "0") == "1" else ""
    udp_disabled = "" if udp_checked else " disabled"
    body = f"""
    <div id="sip" class="tab-content active">
        <div class="info-card login-settings">
            <p>{h(ctx["product_name"])} uses Session Initiation Protocol (SIP) to integrate with PBXes and phone systems, connect to consoles, and to ATAs.</p>
            <form id="sipSettingsForm">
                <div class="info-row" style="border-bottom:none; padding-bottom:0;">
                    <span class="info-label">Enable SIP over UDP/TCP</span>
                    <span><label class="switch"><input type="checkbox" name="enable_insecure_sip" id="udpToggle"{udp_checked}><span class="slider"></span></label></span>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; margin-bottom:16px;">
                    <span class="info-label">Port</span>
                    <input type="number" name="insecure_sip_port" id="udpPort" min="1" max="65535" value="{h(data.get("insecure_sip_port", "5060"))}"{udp_disabled}>
                    <span id="udpPortError" class="port-error-text">Please enter a valid port (1-65535).</span>
                </div>
                <input type="hidden" name="save_sip_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveSipBtn">Save Settings</button>
                    <span id="sip-save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>"""
    script = r"""
document.addEventListener('DOMContentLoaded', function() {
    const udpToggle = document.getElementById('udpToggle');
    const udpPortInput = document.getElementById('udpPort');
    const udpPortError = document.getElementById('udpPortError');
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
    [udpPortInput].forEach(input => input.addEventListener('input', function() {
        if (this.value > 65535) this.value = 65535;
        if (this.value.length > 5) this.value = this.value.slice(0, 5);
        validatePortInput(this, udpPortError);
    }));
    udpToggle.addEventListener('change', function() {
        udpPortInput.disabled = !this.checked;
        if (this.checked) validatePortInput(udpPortInput, udpPortError);
        else { udpPortInput.classList.remove('invalid-port'); udpPortError.style.display = 'none'; }
    });
    postSettings('sipSettingsForm','saveSipBtn','sip-save-status','SIP Settings saved.', false);
});
"""
    return settings_page("SIP Settings", ctx, "sip", body, script)
