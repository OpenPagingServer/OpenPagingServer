import ipaddress
import urllib.request

from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page

SIP_RTP_DEFAULT_PORT_START = "40000"
SIP_RTP_DEFAULT_PORT_END = "50000"


def is_port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.1):
            return True
    except OSError:
        return False


def is_public_routable_ipv4(value):
    try:
        address = ipaddress.IPv4Address(str(value or "").strip())
    except Exception:
        return False
    return bool(address.is_global)


def clean_sip_nat_support_mode(value):
    token = str(value if value is not None else "").strip().lower()
    return "no" if token in {"no", "off", "disable", "disabled", "0", "false"} else "auto"


def detect_external_ipv4():
    try:
        request_obj = urllib.request.Request(
            "https://analytics.openpagingserver.org/ipaddr",
            headers={"User-Agent": "OpenPagingServer"},
        )
        with urllib.request.urlopen(request_obj, timeout=3) as response:
            payload = response.read().decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""
    return payload if is_public_routable_ipv4(payload) else ""


def ensure_sip_rtp_port_settings(data):
    if not str(data.get("sip_rtp_port_start", "") or "").strip():
        save_setting("sip_rtp_port_start", SIP_RTP_DEFAULT_PORT_START, "SIP RTP port range start")
        data["sip_rtp_port_start"] = SIP_RTP_DEFAULT_PORT_START
    if not str(data.get("sip_rtp_port_end", "") or "").strip():
        save_setting("sip_rtp_port_end", SIP_RTP_DEFAULT_PORT_END, "SIP RTP port range end")
        data["sip_rtp_port_end"] = SIP_RTP_DEFAULT_PORT_END


def sip_settings_body(ctx, data, detected_external_ipv4):
    udp_checked = " checked" if data.get("enable_insecure_sip", "0") == "1" else ""
    udp_disabled = "" if udp_checked else " disabled"
    nat_enabled = clean_sip_nat_support_mode(data.get("sip_nat_support", "1")) != "no"
    nat_checked = " checked" if nat_enabled else ""
    external_mode = str(data.get("sip_external_ipv4_mode", "auto") or "auto").strip().lower()
    if external_mode not in {"auto", "manual"}:
        external_mode = "auto"
    manual_external_ipv4 = str(data.get("sip_external_ipv4", "") or "").strip()
    displayed_external_ipv4 = detected_external_ipv4 if external_mode == "auto" else manual_external_ipv4
    external_field_style = "background:rgba(0,0,0,0.05); color:#777;" if external_mode == "auto" else ""
    return f"""
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
                <div class="info-row" style="align-items:center; gap:16px; flex-wrap:wrap;">
                    <span class="info-label">RTP Port Range</span>
                    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-left:auto;">
                        <input type="number" name="sip_rtp_port_start" id="rtpPortStart" min="1024" max="65535" value="{h(data.get("sip_rtp_port_start", SIP_RTP_DEFAULT_PORT_START))}" style="width:120px;">
                        <span>-</span>
                        <input type="number" name="sip_rtp_port_end" id="rtpPortEnd" min="1024" max="65535" value="{h(data.get("sip_rtp_port_end", SIP_RTP_DEFAULT_PORT_END))}" style="width:120px;">
                    </div>
                </div>
                <div style="margin:-8px 0 16px 0;">
                    <span id="rtpPortError" class="port-error-text">Please enter a valid RTP port range within 1024-65535.</span>
                </div>
                <div class="info-row" style="border-bottom:none; padding-bottom:0;">
                    <span>
                        <span class="info-label">NAT Support</span>
                        <span class="info-description">When enabled, Open Paging Server will be able to advertise the external IP address of this server and use Symmetric RTP if a call is crossing NAT.</span>
                    </span>
                    <span><label class="switch"><input type="checkbox" name="sip_nat_support" id="natToggle"{nat_checked}><span class="slider"></span></label></span>
                </div>
                <div id="natOptions">
                    <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 10px; margin-bottom:16px;">
                        <span class="info-label">External IPv4</span>
                        <label style="display:flex; align-items:center; gap:8px; cursor:pointer;">
                            <input type="radio" name="sip_external_ipv4_mode" id="externalIpv4ModeAuto" value="auto"{" checked" if external_mode == "auto" else ""}>
                            <span>Detect automatically</span>
                        </label>
                        <label style="display:flex; align-items:center; gap:8px; cursor:pointer;">
                            <input type="radio" name="sip_external_ipv4_mode" id="externalIpv4ModeManual" value="manual"{" checked" if external_mode == "manual" else ""}>
                            <span>Manual</span>
                        </label>
                        <input type="text" name="sip_external_ipv4" id="externalIpv4Field" value="{h(displayed_external_ipv4)}" data-auto-value="{h(detected_external_ipv4)}" data-manual-value="{h(manual_external_ipv4)}" placeholder="203.0.113.10"{" readonly" if external_mode == "auto" else ""} style="{h(external_field_style)}">
                        <span id="externalIpv4Error" class="port-error-text">Please enter a valid publicly routable IPv4 address.</span>
                    </div>
                </div>
                <input type="hidden" name="save_sip_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveSipBtn">Save Settings</button>
                    <span id="sip-save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>"""


SCRIPT = r"""
document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('sipSettingsForm');
    const saveBtn = document.getElementById('saveSipBtn');
    const status = document.getElementById('sip-save-status');
    const udpToggle = document.getElementById('udpToggle');
    const udpPort = document.getElementById('udpPort');
    const udpPortError = document.getElementById('udpPortError');
    const natToggle = document.getElementById('natToggle');
    const natOptions = document.getElementById('natOptions');
    const externalIpv4ModeAuto = document.getElementById('externalIpv4ModeAuto');
    const externalIpv4ModeManual = document.getElementById('externalIpv4ModeManual');
    const externalIpv4Field = document.getElementById('externalIpv4Field');
    const externalIpv4Error = document.getElementById('externalIpv4Error');
    const rtpPortStart = document.getElementById('rtpPortStart');
    const rtpPortEnd = document.getElementById('rtpPortEnd');
    const rtpPortError = document.getElementById('rtpPortError');
    const autoExternalIpv4Value = externalIpv4Field.dataset.autoValue || '';
    let manualExternalIpv4Value = externalIpv4Field.dataset.manualValue || '';

    function setStatus(message, color) {
        status.innerText = message || '';
        status.style.color = color || 'inherit';
    }

    function clampPortInput(input, minValue) {
        if (!input) return;
        if (input.value !== '') {
            const numeric = Number(input.value);
            if (Number.isFinite(numeric) && numeric > 65535) input.value = '65535';
            if (input.value.length > 5) input.value = input.value.slice(0, 5);
            if (Number.isFinite(numeric) && numeric < minValue) input.value = String(minValue);
        }
    }

    function validatePortInput(input, errorElement, minValue) {
        if (!input || !errorElement) return true;
        const numeric = Number(input.value);
        if (input.value === '' || !Number.isFinite(numeric) || numeric < minValue || numeric > 65535) {
            input.classList.add('invalid-port');
            errorElement.style.display = 'block';
            return false;
        }
        input.classList.remove('invalid-port');
        errorElement.style.display = 'none';
        return true;
    }

    function parseIpv4(value) {
        const parts = String(value || '').trim().split('.');
        if (parts.length !== 4) return null;
        const octets = [];
        for (const part of parts) {
            if (!/^\d+$/.test(part)) return null;
            const numeric = Number(part);
            if (!Number.isFinite(numeric) || numeric < 0 || numeric > 255) return null;
            octets.push(numeric);
        }
        return octets;
    }

    function isPublicRoutableIpv4(value) {
        const octets = parseIpv4(value);
        if (!octets) return false;
        const [a, b, c] = octets;
        if (a === 0 || a === 10 || a === 127 || a >= 224) return false;
        if (a === 100 && b >= 64 && b <= 127) return false;
        if (a === 169 && b === 254) return false;
        if (a === 172 && b >= 16 && b <= 31) return false;
        if (a === 192 && b === 168) return false;
        if (a === 192 && b === 0 && c === 0) return false;
        if (a === 192 && b === 0 && c === 2) return false;
        if (a === 198 && (b === 18 || b === 19)) return false;
        if (a === 198 && b === 51 && c === 100) return false;
        if (a === 203 && b === 0 && c === 113) return false;
        if (a === 255) return false;
        return true;
    }

    function validateExternalIpv4() {
        if (!natToggle.checked || externalIpv4ModeAuto.checked) {
            externalIpv4Field.classList.remove('invalid-port');
            externalIpv4Error.style.display = 'none';
            return true;
        }
        if (!isPublicRoutableIpv4(externalIpv4Field.value)) {
            externalIpv4Field.classList.add('invalid-port');
            externalIpv4Error.style.display = 'block';
            return false;
        }
        externalIpv4Field.classList.remove('invalid-port');
        externalIpv4Error.style.display = 'none';
        return true;
    }

    function validateRtpRange() {
        const start = Number(rtpPortStart.value);
        const end = Number(rtpPortEnd.value);
        if (
            rtpPortStart.value === '' ||
            rtpPortEnd.value === '' ||
            !Number.isFinite(start) ||
            !Number.isFinite(end) ||
            start < 1024 ||
            start > 65535 ||
            end < 1024 ||
            end > 65535 ||
            start > end
        ) {
            rtpPortStart.classList.add('invalid-port');
            rtpPortEnd.classList.add('invalid-port');
            rtpPortError.style.display = 'block';
            return false;
        }
        rtpPortStart.classList.remove('invalid-port');
        rtpPortEnd.classList.remove('invalid-port');
        rtpPortError.style.display = 'none';
        return true;
    }

    function syncUdp() {
        udpPort.disabled = !udpToggle.checked;
        if (udpToggle.checked) validatePortInput(udpPort, udpPortError, 1);
        else {
            udpPort.classList.remove('invalid-port');
            udpPortError.style.display = 'none';
        }
    }

    function syncExternalIpv4Field() {
        const autoSelected = externalIpv4ModeAuto.checked;
        if (autoSelected) {
            externalIpv4Field.value = autoExternalIpv4Value;
            externalIpv4Field.readOnly = true;
            externalIpv4Field.style.background = 'rgba(0,0,0,0.05)';
            externalIpv4Field.style.color = '#777';
        } else {
            externalIpv4Field.value = manualExternalIpv4Value;
            externalIpv4Field.readOnly = false;
            externalIpv4Field.style.background = '';
            externalIpv4Field.style.color = '';
        }
        validateExternalIpv4();
    }

    function syncNatOptions() {
        natOptions.style.display = natToggle.checked ? 'block' : 'none';
        syncExternalIpv4Field();
    }

    function validateForm() {
        const udpValid = !udpToggle.checked || validatePortInput(udpPort, udpPortError, 1);
        const natValid = validateExternalIpv4();
        const rtpValid = validateRtpRange();
        return udpValid && natValid && rtpValid;
    }

    [udpPort, rtpPortStart, rtpPortEnd].forEach(input => {
        input.addEventListener('input', function() {
            clampPortInput(this, this === udpPort ? 1 : 1024);
            if (this === udpPort) validatePortInput(udpPort, udpPortError, 1);
            else validateRtpRange();
        });
    });
    externalIpv4Field.addEventListener('input', function() {
        manualExternalIpv4Value = externalIpv4Field.value;
        validateExternalIpv4();
    });
    udpToggle.addEventListener('change', syncUdp);
    natToggle.addEventListener('change', syncNatOptions);
    externalIpv4ModeAuto.addEventListener('change', syncExternalIpv4Field);
    externalIpv4ModeManual.addEventListener('change', syncExternalIpv4Field);

    saveBtn.addEventListener('click', function() {
        if (window.openDemoModePopup && document.querySelector('[data-demo-mode="1"]')) {
            openDemoModePopup('settings');
            return;
        }
        if (!validateForm()) {
            setStatus('Fix the highlighted SIP settings first.', '#F44336');
            setTimeout(function() { setStatus('', 'inherit'); }, 3000);
            return;
        }
        const formData = new FormData(form);
        saveBtn.disabled = true;
        setStatus('Saving...', 'inherit');
        fetch(window.location.href, {
            method: 'POST',
            body: formData,
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                setStatus('SIP Settings saved.', '#4CAF50');
            } else {
                setStatus(data.message || 'Error saving settings.', '#F44336');
            }
        })
        .catch(function() {
            setStatus('Connection error.', '#F44336');
        })
        .finally(function() {
            saveBtn.disabled = false;
            setTimeout(function() { setStatus('', 'inherit'); }, 3000);
        });
    });

    syncUdp();
    syncNatOptions();
    validateRtpRange();
});
"""


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    ensure_sip_rtp_port_settings(data)
    detected_external_ipv4 = detect_external_ipv4()
    if request.method == "POST":
        if demo_mode_enabled():
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="error", message="Demo Mode is enabled.")
            return demo_mode_page("SIP Settings", legacy_user_context(user), "settings", "settings")
        udp_enabled = "1" if request.form.get("enable_insecure_sip") else "0"
        udp_port = request.form.get("insecure_sip_port", "5060").strip()
        nat_support = "1" if request.form.get("sip_nat_support") else "0"
        external_mode = str(request.form.get("sip_external_ipv4_mode", "auto") or "auto").strip().lower()
        stored_manual_external_ipv4 = str(data.get("sip_external_ipv4", "") or "").strip()
        manual_external_ipv4 = request.form.get("sip_external_ipv4", stored_manual_external_ipv4).strip() if external_mode == "manual" else stored_manual_external_ipv4
        rtp_port_start = request.form.get("sip_rtp_port_start", SIP_RTP_DEFAULT_PORT_START).strip()
        rtp_port_end = request.form.get("sip_rtp_port_end", SIP_RTP_DEFAULT_PORT_END).strip()
        tls_enabled = data.get("enable_secure_sip", "0")
        errors = []

        if external_mode not in {"auto", "manual"}:
            external_mode = "auto"

        try:
            udp_port_number = int(udp_port)
            if udp_port_number < 1 or udp_port_number > 65535:
                errors.append("Invalid SIP port.")
            elif udp_enabled == "1" and str(udp_port) != str(data.get("insecure_sip_port", "5060")) and is_port_in_use(udp_port):
                errors.append(f"Port {udp_port} is already in use.")
        except ValueError:
            errors.append("Invalid SIP port.")

        try:
            rtp_start_number = int(rtp_port_start)
            rtp_end_number = int(rtp_port_end)
            if rtp_start_number < 1024 or rtp_start_number > 65535 or rtp_end_number < 1024 or rtp_end_number > 65535:
                errors.append("RTP Port Range must stay within 1024-65535.")
            elif rtp_start_number > rtp_end_number:
                errors.append("RTP Port Range start must be less than or equal to the end.")
        except ValueError:
            errors.append("RTP Port Range is invalid.")

        if nat_support != "0" and external_mode == "manual" and not is_public_routable_ipv4(manual_external_ipv4):
            errors.append("Enter a valid publicly routable External IPv4 address.")

        if errors:
            message = " ".join(errors)
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="error", message=message)
            ctx = legacy_user_context(user)
            error_data = dict(data)
            error_data.update(
                {
                    "enable_insecure_sip": udp_enabled,
                    "insecure_sip_port": udp_port,
                    "sip_nat_support": nat_support,
                    "sip_external_ipv4_mode": external_mode,
                    "sip_external_ipv4": manual_external_ipv4,
                    "sip_rtp_port_start": rtp_port_start,
                    "sip_rtp_port_end": rtp_port_end,
                }
            )
            return settings_page(
                "SIP Settings",
                ctx,
                "sip",
                f'<div class="info-card login-settings"><p>{h(message)}</p></div>' + sip_settings_body(ctx, error_data, detected_external_ipv4),
                SCRIPT,
            )

        save_setting("sip", "1" if (udp_enabled == "1" or tls_enabled == "1") else "0", "Enable SIP")
        save_setting("enable_insecure_sip", udp_enabled, "Enable SIP over UDP/TCP")
        save_setting("insecure_sip_port", udp_port, "SIP UDP/TCP Port")
        save_setting("sip_nat_support", nat_support, "Enable NAT support for SIP (0/1)")
        save_setting("sip_external_ipv4_mode", external_mode, "SIP external IPv4 mode (auto/manual)")
        save_setting("sip_external_ipv4", manual_external_ipv4, "Manual SIP external IPv4 address")
        save_setting("sip_rtp_port_start", rtp_port_start, "SIP RTP port range start")
        save_setting("sip_rtp_port_end", rtp_port_end, "SIP RTP port range end")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/sip")

    ctx = legacy_user_context(user)
    return settings_page("SIP Settings", ctx, "sip", sip_settings_body(ctx, data, detected_external_ipv4), SCRIPT)
