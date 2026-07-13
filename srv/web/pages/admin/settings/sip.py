import hmac
import ipaddress
import os
import urllib.request

from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page

SIP_RTP_DEFAULT_PORT_START = "40000"
SIP_RTP_DEFAULT_PORT_END = "50000"
SIP_BLOCK_SCANNERS_SETTING = "sip_block_scanners"
SIP_INTRUSION_PREVENTION_SETTING = "sip_intrusion_prevention"
SIP_SECURITY_FALSE_VALUES = {"0", "false", "off", "disable", "disabled", "no"}
SIP_SECURITY_UNLOCK_SESSION_KEY = "sip_security_unlock_until"
SIP_SECURITY_UNLOCK_IP_SESSION_KEY = "sip_security_unlock_ip"
SIP_SECURITY_UNLOCK_PAGE_TOKEN_SESSION_KEY = "sip_security_unlock_page_token"
SIP_SECURITY_UNLOCK_PAGE_TOKEN_FORM_KEY = "sip_security_unlock_page_token"
SIP_SECURITY_UNLOCK_TTL_SECONDS = 300
SIP_SENSITIVE_VERIFY_SESSION_KEY = "sip_sensitive_verify_until"
SIP_SENSITIVE_VERIFY_CHALLENGE_KEY = "sip_sensitive_verify_challenge"
SIP_SENSITIVE_VERIFY_TTL_SECONDS = 180


def sip_abuse_override_enabled():
    return str(os.getenv("ALLOW_SIP_ABUSE", "") or "").strip().lower() == "true"


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


def setting_enabled_with_default(data, key, default=True):
    token = str(data.get(key, "") or "").strip().lower()
    if not token:
        return bool(default)
    return token not in SIP_SECURITY_FALSE_VALUES


def normalize_toggle_value(value, default="1"):
    token = str(value if value is not None else default).strip().lower()
    return "0" if token in SIP_SECURITY_FALSE_VALUES else "1"


def sip_security_unlock_hash():
    return "2a6ad5390bc20f6102ea8c207b9f6c4dd7e636b6a2dbec1b7b45f65ac3792c271384042a1ccd9f92096363fc701ba3c3a531736309e0d2f95f110aa0931f8763"


def sip_securitydowngrade_warning():
    return (
        "Disabling this setting WILL compromise the security of this server, especially if the SIP port is exposed to WAN. "
        "There's usually no reason to disable this in production. The Open Paging Server project is NOT responsible for any "
        "financial loss caused by abuse of telephone service by malicious bots. CONTINUE AT YOUR OWN RISK!!!"
    )


def effective_sip_security_enabled(data, key, default=True):
    if not sip_abuse_override_enabled():
        return True
    return setting_enabled_with_default(data, key, default=default)


def ensure_sip_rtp_port_settings(data):
    if not str(data.get("sip_rtp_port_start", "") or "").strip():
        save_setting("sip_rtp_port_start", SIP_RTP_DEFAULT_PORT_START, "SIP RTP port range start")
        data["sip_rtp_port_start"] = SIP_RTP_DEFAULT_PORT_START
    if not str(data.get("sip_rtp_port_end", "") or "").strip():
        save_setting("sip_rtp_port_end", SIP_RTP_DEFAULT_PORT_END, "SIP RTP port range end")
        data["sip_rtp_port_end"] = SIP_RTP_DEFAULT_PORT_END


def ensure_sip_security_settings(data):
    defaults = [
        (SIP_BLOCK_SCANNERS_SETTING, "1", "Block SIP scanner user agents"),
        (SIP_INTRUSION_PREVENTION_SETTING, "1", "Block IPs after repeated unauthorized SIP REGISTER/INVITE attempts"),
    ]
    for key, value, description in defaults:
        if not str(data.get(key, "") or "").strip():
            save_setting(key, value, description)
            data[key] = value


def sip_security_client_ip():
    return str(request.remote_addr or "").strip()


def sip_security_unlock_active():
    try:
        expires_at = float(session.get(SIP_SECURITY_UNLOCK_SESSION_KEY, "0") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at > time.time():
        return True
    clear_sip_security_unlock()
    return False


def sip_unauthorized_response():
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(status="error", message="Unauthorized"), 401
    abort(401)


def clear_sip_security_unlock():
    session.pop(SIP_SECURITY_UNLOCK_SESSION_KEY, None)
    session.pop(SIP_SECURITY_UNLOCK_IP_SESSION_KEY, None)


def clear_sip_security_page_token():
    session.pop(SIP_SECURITY_UNLOCK_PAGE_TOKEN_SESSION_KEY, None)


def issue_sip_security_page_token():
    token = secrets.token_urlsafe(32)
    session[SIP_SECURITY_UNLOCK_PAGE_TOKEN_SESSION_KEY] = token
    clear_sip_security_unlock()
    return token


def sip_security_page_token_active():
    supplied = str(request.form.get(SIP_SECURITY_UNLOCK_PAGE_TOKEN_FORM_KEY) or "").strip()
    stored = str(session.get(SIP_SECURITY_UNLOCK_PAGE_TOKEN_SESSION_KEY, "") or "").strip()
    return bool(supplied and stored and hmac.compare_digest(supplied, stored))


def sip_sensitive_verify_active():
    try:
        expires_at = float(session.get(SIP_SENSITIVE_VERIFY_SESSION_KEY, "0") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at > time.time():
        return True
    session.pop(SIP_SENSITIVE_VERIFY_SESSION_KEY, None)
    return False


def clear_sip_sensitive_verify():
    session.pop(SIP_SENSITIVE_VERIFY_SESSION_KEY, None)
    session.pop(SIP_SENSITIVE_VERIFY_CHALLENGE_KEY, None)


def current_user_auth_record(user):
    return query_one(
        "SELECT id, username, password, salt FROM users WHERE id=%s LIMIT 1",
        (user.get("id"),),
    ) or {}


def sip_unlock_candidate_matches(value):
    candidate = str(value or "")
    digest = hashlib.sha512(candidate.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, sip_security_unlock_hash())


def handle_sip_unlock_request(user, data, detected_external_ipv4):
    if not sip_abuse_override_enabled():
        abort(404)
    supplied = str(request.form.get("unlock_probe") or "")
    matched = bool(supplied) and sip_unlock_candidate_matches(supplied)
    if matched:
        session[SIP_SECURITY_UNLOCK_SESSION_KEY] = str(time.time() + SIP_SECURITY_UNLOCK_TTL_SECONDS)
        session[SIP_SECURITY_UNLOCK_IP_SESSION_KEY] = sip_security_client_ip()
        return jsonify(status="success")
    return jsonify(status="ignored")


def issue_sensitive_change_challenge(user):
    record = current_user_auth_record(user)
    if not record:
        return jsonify(status="error", message="Invalid username or password."), 401
    challenge = secrets.token_hex(32)
    session[SIP_SENSITIVE_VERIFY_CHALLENGE_KEY] = challenge
    session.pop(SIP_SENSITIVE_VERIFY_SESSION_KEY, None)
    return jsonify(
        status="success",
        username=str(record.get("username") or user.get("username") or ""),
        salt=str(record.get("salt") or ""),
        challenge=challenge,
    )


def verify_sensitive_change_response(user):
    record = current_user_auth_record(user)
    challenge = str(session.get(SIP_SENSITIVE_VERIFY_CHALLENGE_KEY, "") or "")
    response = str(request.form.get("response") or "").strip()
    expected = hashlib.sha256((str(record.get("password") or "") + challenge).encode()).hexdigest() if record and challenge else ""
    clear_sip_sensitive_verify()
    if not response or not expected or not hmac.compare_digest(response, expected):
        return jsonify(status="error", message="Invalid username or password."), 401
    session[SIP_SENSITIVE_VERIFY_SESSION_KEY] = str(time.time() + SIP_SENSITIVE_VERIFY_TTL_SECONDS)
    return jsonify(status="success")


def sip_settings_body(ctx, data, detected_external_ipv4, user, unlock_page_token=""):
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
    allow_sip_abuse = sip_abuse_override_enabled()
    scanners_enabled = effective_sip_security_enabled(data, SIP_BLOCK_SCANNERS_SETTING, default=True)
    intrusion_enabled = effective_sip_security_enabled(data, SIP_INTRUSION_PREVENTION_SETTING, default=True)
    security_unlocked = bool(unlock_page_token)
    scanners_locked = scanners_enabled and (not allow_sip_abuse or not security_unlocked)
    intrusion_locked = intrusion_enabled and (not allow_sip_abuse or not security_unlocked)
    scanner_checkbox_attrs = (" checked" if scanners_enabled else "") + (" disabled" if scanners_locked else "")
    intrusion_checkbox_attrs = (" checked" if intrusion_enabled else "") + (" disabled" if intrusion_locked else "")
    scanner_switch_class = "switch locked" if scanners_locked else "switch"
    intrusion_switch_class = "switch locked" if intrusion_locked else "switch"
    scanner_aria_attr = ' aria-disabled="true"' if scanners_locked else ""
    intrusion_aria_attr = ' aria-disabled="true"' if intrusion_locked else ""
    unlock_listener_required = allow_sip_abuse and (scanners_enabled or intrusion_enabled) and not security_unlocked
    username = str(user.get("username") or "")
    if scanners_locked:
        scanner_switch_html = '<span class="switch locked static checked" aria-disabled="true"><span class="slider"></span></span>'
    else:
        scanner_switch_html = f'<label class="{h(scanner_switch_class)}"{scanner_aria_attr}><input type="checkbox" id="blockScannersToggle"{scanner_checkbox_attrs}><span class="slider"></span></label>'
    if intrusion_locked:
        intrusion_switch_html = '<span class="switch locked static checked" aria-disabled="true"><span class="slider"></span></span>'
    else:
        intrusion_switch_html = f'<label class="{h(intrusion_switch_class)}"{intrusion_aria_attr}><input type="checkbox" id="intrusionPreventionToggle"{intrusion_checkbox_attrs}><span class="slider"></span></label>'
    docker_mode = os.environ.get("OPS_DOCKER_MODE", "") == "1"
    docker_port_disabled = " disabled" if docker_mode else ""
    docker_port_style = ' style="background:rgba(0,0,0,0.05); color:#777;"' if docker_mode else ""
    docker_port_notice = '<div style="display:flex; align-items:center; gap:6px; margin-top:4px;"><svg width="16" height="16" viewBox="0 0 24 24" fill="#1976D2"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg><span style="color:#1976D2; font-size:0.85em;">Change port by editing .env in Docker</span></div>' if docker_mode else ""
    return f"""
    <style>
    .switch.locked {{
        opacity:0.45;
        cursor:not-allowed;
    }}
    .switch.static {{
        display:inline-block;
        pointer-events:none;
    }}
    .switch.locked .slider {{
        background-color:#C7CDD3;
    }}
    .switch.locked .slider:before {{
        background-color:#F3F4F6;
    }}
    .switch.static.checked .slider {{
        background-color:#90caf9;
    }}
    .switch.static.checked .slider:before {{
        transform: translateX(20px);
        background-color:#1976D2;
    }}
    .sip-security-divider {{
        border-top:1px solid #E5E5E5;
        margin:8px 0 12px 0;
    }}
    .sip-sensitive-modal-backdrop {{
        display:none;
        position:fixed;
        inset:0;
        background:rgba(0,0,0,0.45);
        z-index:1600;
        align-items:center;
        justify-content:center;
        padding:18px;
        box-sizing:border-box;
    }}
    .sip-sensitive-modal-backdrop.active {{
        display:flex;
    }}
    .sip-sensitive-modal {{
        max-width:390px;
        width:min(92vw, 390px);
    }}
    .sip-sensitive-login-box {{
        background:#fff;
        padding:30px;
        border-radius:6px;
        box-shadow:0 4px 6px rgba(0,0,0,0.1),0 1px 3px rgba(0,0,0,0.08);
        max-width:390px;
        width:min(92vw, 390px);
        text-align:center;
        animation:fadeInPage 1.5s ease-in-out;
    }}
    .sip-sensitive-login-box h2 {{
        margin:0 0 20px 0;
        color:#1976D2;
        font-weight:500;
        font-size:1.5em;
    }}
    .sip-sensitive-input-field {{
        position:relative;
        margin-bottom:20px;
    }}
    .sip-sensitive-input-field input {{
        width:100%;
        padding:8px 0;
        border:none;
        border-bottom:2px solid #ccc;
        font-size:16px;
        background:transparent;
        outline:none;
        color:#333;
        font-family:"Roboto", sans-serif;
        box-sizing:border-box;
    }}
    .sip-sensitive-input-field input:focus {{
        border-bottom:2px solid #1976d2;
    }}
    .sip-sensitive-input-field input[disabled] {{
        color:#999;
        border-bottom:2px solid #ddd;
    }}
    .sip-sensitive-input-field label {{
        position:absolute;
        top:8px;
        left:0;
        color:#888;
        font-size:14px;
        pointer-events:none;
        transition:0.2s ease all;
    }}
    .sip-sensitive-input-field input:focus ~ label,
    .sip-sensitive-input-field input:not(:placeholder-shown) ~ label {{
        top:-16px;
        left:0;
        font-size:12px;
        color:#1976d2;
    }}
    .sip-sensitive-actions {{
        display:flex;
        flex-direction:column;
        align-items:center;
        gap:10px;
        margin-top:10px;
    }}
    .sip-sensitive-actions button {{
        width:100%;
        padding:12px;
        background-color:#1976d2;
        border:none;
        color:#fff;
        font-size:16px;
        border-radius:4px;
        cursor:pointer;
        font-family:"Roboto", sans-serif;
        text-transform:uppercase;
        height:45px;
        display:inline-flex;
        align-items:center;
        justify-content:center;
    }}
    .sip-sensitive-actions button:disabled {{
        opacity:0.7;
        cursor:default;
    }}
    .sip-sensitive-cancel {{
        color:#1976d2;
        text-decoration:none;
        font-size:14px;
        line-height:1.4;
    }}
    .sip-sensitive-cancel:hover {{
        text-decoration:underline;
    }}
    .sip-sensitive-error {{
        min-height:1.2em;
        color:#d32f2f;
        font-size:0.9em;
        margin-top:10px;
    }}
    @media (max-width:768px) {{
        .sip-sensitive-login-box {{
            max-width:360px;
            width:100%;
            padding:22px;
        }}
    }}
    @media(prefers-color-scheme:dark) {{
        .switch.locked .slider {{
            background-color:#3A3A3A;
        }}
        .switch.locked .slider:before {{
            background-color:#666;
        }}
        .switch.static.checked .slider {{
            background-color:#3d2b52;
        }}
        .switch.static.checked .slider:before {{
            background-color:#BB86FC;
        }}
        .sip-security-divider {{
            border-top-color:#333;
        }}
        .sip-sensitive-login-box {{
            background:#1E1E1E;
            box-shadow:0 4px 6px rgba(0,0,0,0.6);
        }}
        .sip-sensitive-login-box h2 {{
            color:#fff;
        }}
        .sip-sensitive-input-field input {{
            color:#fff;
            border-bottom:2px solid #555;
        }}
        .sip-sensitive-input-field input[disabled] {{
            color:#777;
            border-bottom:2px solid #444;
        }}
        .sip-sensitive-input-field label {{
            color:#BBB;
        }}
        .sip-sensitive-actions button {{
            background-color:#90caf9;
            color:#121212;
        }}
        .sip-sensitive-cancel {{
            color:#90caf9;
        }}
        .sip-sensitive-error {{
            color:#ffcdd2;
        }}
    }}
    </style>
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
                    <input type="number" name="insecure_sip_port" id="udpPort" min="1" max="65535" value="{h(data.get("insecure_sip_port", "5060"))}"{docker_port_disabled if docker_mode else udp_disabled}{docker_port_style}>
                    {docker_port_notice}
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
                <div class="sip-security-divider"></div>
                <div class="info-row" style="border-bottom:none; padding-bottom:0;">
                    <span>
                        <span class="info-label">Block Scanners</span>
                        <span class="info-description">Drop requests with user agents associated with SIP scanners. There's usually no reason to disable this in production.</span>
                    </span>
                    <span>
                        <input type="hidden" name="{h(SIP_BLOCK_SCANNERS_SETTING)}" id="blockScannersValue" value="{"1" if scanners_enabled else "0"}">
                        {scanner_switch_html}
                    </span>
                </div>
                <div class="info-row" style="border-bottom:none; padding-bottom:0;">
                    <span>
                        <span class="info-label">Intrusion Detection/Prevention</span>
                        <span class="info-description">Block IP addresses for 48 hours if they send 5 unauthorized REGISTER and/or INVITE attempts within a span 5 minutes. There's usually no reason to disable this in production.</span>
                    </span>
                    <span>
                        <input type="hidden" name="{h(SIP_INTRUSION_PREVENTION_SETTING)}" id="intrusionPreventionValue" value="{"1" if intrusion_enabled else "0"}">
                        {intrusion_switch_html}
                    </span>
                </div>
                <input type="hidden" name="save_sip_settings" value="1">
                <input type="hidden" name="{h(SIP_SECURITY_UNLOCK_PAGE_TOKEN_FORM_KEY)}" id="sipSecurityUnlockPageToken" value="{h(unlock_page_token)}">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveSipBtn">Save Settings</button>
                    <span id="sip-save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/js-sha256@0.9.0/src/sha256.min.js"></script>
    <div id="sipSensitiveModal" class="sip-sensitive-modal-backdrop" aria-hidden="true">
        <div class="sip-sensitive-modal">
            <div class="sip-sensitive-login-box">
            <h2>Please verify your identity to make sensitive changes</h2>
            <form id="sipSensitiveVerifyForm">
                <div class="sip-sensitive-input-field">
                    <input id="sipSensitiveUsername" type="text" value="{h(username)}" placeholder=" " disabled>
                    <label for="sipSensitiveUsername">Username</label>
                </div>
                <div class="sip-sensitive-input-field">
                    <input id="sipSensitivePassword" type="password" autocomplete="current-password" placeholder=" " required>
                    <label for="sipSensitivePassword">Password</label>
                </div>
                <div id="sipSensitiveError" class="sip-sensitive-error"></div>
                <div class="sip-sensitive-actions">
                    <button type="submit" id="confirmSipSensitiveModal">LOGIN</button>
                    <a href="#" class="sip-sensitive-cancel" id="closeSipSensitiveModal">Cancel</a>
                </div>
            </form>
            </div>
        </div>
    </div>
    <input type="hidden" id="sipUnlockRequired" value="{"1" if unlock_listener_required else "0"}">
    """


SCRIPT_TEMPLATE = r"""
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
    const blockScannersToggle = document.getElementById('blockScannersToggle');
    const blockScannersValue = document.getElementById('blockScannersValue');
    const intrusionPreventionToggle = document.getElementById('intrusionPreventionToggle');
    const intrusionPreventionValue = document.getElementById('intrusionPreventionValue');
    const sensitiveModal = document.getElementById('sipSensitiveModal');
    const sensitiveForm = document.getElementById('sipSensitiveVerifyForm');
    const sensitivePassword = document.getElementById('sipSensitivePassword');
    const sensitiveError = document.getElementById('sipSensitiveError');
    const closeSensitiveBtn = document.getElementById('closeSipSensitiveModal');
    const sipSecurityUnlockPageToken = document.getElementById('sipSecurityUnlockPageToken');
    const autoExternalIpv4Value = externalIpv4Field.dataset.autoValue || '';
    let manualExternalIpv4Value = externalIpv4Field.dataset.manualValue || '';
    let sensitiveVerified = false;
    const sipDisableWarning = __SIP_DISABLE_WARNING__;
    const scannerInitiallyEnabled = __SCANNER_INITIALLY_ENABLED__;
    const intrusionInitiallyEnabled = __INTRUSION_INITIALLY_ENABLED__;
    const scannerCanDisable = __SCANNER_CAN_DISABLE__;
    const intrusionCanDisable = __INTRUSION_CAN_DISABLE__;
__UNLOCK_RUNTIME_DECLARATIONS__

    function setStatus(message, color) {
        status.innerText = message || '';
        status.style.color = color || 'inherit';
    }

    async function readJsonResponse(response, fallbackMessage) {
        const contentType = String(response.headers.get('content-type') || '').toLowerCase();
        if (!contentType.includes('application/json')) {
            throw new Error(fallbackMessage);
        }
        const data = await response.json();
        if (!response.ok) {
            throw new Error((data && data.message) ? data.message : fallbackMessage);
        }
        return data;
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

    function syncSecurityHiddenValues() {
        if (blockScannersToggle && blockScannersValue) {
            blockScannersValue.value = blockScannersToggle.checked ? '1' : '0';
        }
        if (intrusionPreventionToggle && intrusionPreventionValue) {
            intrusionPreventionValue.value = intrusionPreventionToggle.checked ? '1' : '0';
        }
    }

    function disablingSensitiveSetting() {
        const scannerDisabling = scannerInitiallyEnabled && blockScannersValue && blockScannersValue.value === '0';
        const intrusionDisabling = intrusionInitiallyEnabled && intrusionPreventionValue && intrusionPreventionValue.value === '0';
        return scannerDisabling || intrusionDisabling;
    }

    function openSensitiveModal() {
        if (!sensitiveModal) return;
        sensitiveError.innerText = '';
        sensitivePassword.value = '';
        sensitiveModal.classList.add('active');
        sensitiveModal.setAttribute('aria-hidden', 'false');
        setTimeout(function() {
            if (sensitivePassword) sensitivePassword.focus();
        }, 0);
    }

    function closeSensitiveModal() {
        if (!sensitiveModal) return;
        sensitiveModal.classList.remove('active');
        sensitiveModal.setAttribute('aria-hidden', 'true');
    }

    function localSha256(value) {
        if (typeof window.sha256 !== 'function') {
            throw new Error('Unable to verify password right now.');
        }
        return window.sha256(String(value || ''));
    }

    async function submitSettings() {
        const formData = new FormData(form);
        saveBtn.disabled = true;
        setStatus('Saving...', 'inherit');
        try {
            const response = await fetch(window.location.href, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            const data = await readJsonResponse(response, 'Error saving settings.');
            if (data.status === 'success') {
                sensitiveVerified = false;
                setStatus('SIP Settings saved.', '#4CAF50');
            } else if (data.status === 'verify_required') {
                setStatus('', 'inherit');
                openSensitiveModal();
            } else {
                setStatus(data.message || 'Error saving settings.', '#F44336');
            }
        } catch (_error) {
            setStatus('Connection error.', '#F44336');
        } finally {
            saveBtn.disabled = false;
            setTimeout(function() { setStatus('', 'inherit'); }, 3000);
        }
    }

    async function verifySensitiveChange() {
        if (!sensitivePassword.value) {
            sensitiveError.innerText = 'Enter your password.';
            return;
        }
        sensitiveError.innerText = '';
        const verifyButton = document.getElementById('confirmSipSensitiveModal');
        verifyButton.disabled = true;
        try {
            const challengeResponse = await fetch(window.location.href, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: new URLSearchParams({ get_sensitive_verify_challenge: '1', sip_security_unlock_page_token: sipSecurityUnlockPageToken ? sipSecurityUnlockPageToken.value : '' })
            });
            const challengeData = await readJsonResponse(challengeResponse, 'Unauthorized');
            if (challengeData.status !== 'success') {
                throw new Error(challengeData.message || 'Invalid username or password.');
            }
            const verifier = localSha256(sensitivePassword.value + (challengeData.salt || ''));
            const proof = localSha256(verifier + (challengeData.challenge || ''));
            const verifyResponse = await fetch(window.location.href, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: new URLSearchParams({ verify_sensitive_change: '1', response: proof, sip_security_unlock_page_token: sipSecurityUnlockPageToken ? sipSecurityUnlockPageToken.value : '' })
            });
            const verifyData = await readJsonResponse(verifyResponse, 'Unauthorized');
            if (verifyData.status !== 'success') {
                throw new Error(verifyData.message || 'Invalid username or password.');
            }
            sensitiveVerified = true;
            closeSensitiveModal();
            await submitSettings();
        } catch (error) {
            sensitiveError.innerText = (error && error.message) ? error.message : 'Invalid username or password.';
        } finally {
            verifyButton.disabled = false;
        }
    }

    function bindSensitiveToggle(toggle, canDisable, initiallyEnabled, hiddenInput) {
        if (!toggle || !hiddenInput) return;
        if (initiallyEnabled && !canDisable) {
            hiddenInput.value = '1';
            return;
        }
        toggle.addEventListener('change', function() {
            if (initiallyEnabled && !this.checked) {
                if (!window.confirm(sipDisableWarning)) {
                    this.checked = true;
                }
            }
            hiddenInput.value = this.checked ? '1' : '0';
            if (this.checked) {
                sensitiveVerified = false;
            }
        });
        hiddenInput.value = toggle.checked ? '1' : '0';
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

    bindSensitiveToggle(blockScannersToggle, scannerCanDisable, scannerInitiallyEnabled, blockScannersValue);
    bindSensitiveToggle(intrusionPreventionToggle, intrusionCanDisable, intrusionInitiallyEnabled, intrusionPreventionValue);

    saveBtn.addEventListener('click', async function() {
        if (window.openDemoModePopup && document.querySelector('[data-demo-mode="1"]')) {
            openDemoModePopup('settings');
            return;
        }
        syncSecurityHiddenValues();
        if (!validateForm()) {
            setStatus('Fix the highlighted SIP settings first.', '#F44336');
            setTimeout(function() { setStatus('', 'inherit'); }, 3000);
            return;
        }
        if (disablingSensitiveSetting() && !sensitiveVerified) {
            openSensitiveModal();
            return;
        }
        await submitSettings();
    });

    if (sensitiveForm) {
        sensitiveForm.addEventListener('submit', async function(event) {
            event.preventDefault();
            await verifySensitiveChange();
        });
    }
    if (closeSensitiveBtn) {
        closeSensitiveBtn.addEventListener('click', function(event) {
            event.preventDefault();
            closeSensitiveModal();
        });
    }
    document.addEventListener('click', function(event) {
        if (event.target === sensitiveModal) {
            closeSensitiveModal();
        }
    });

__UNLOCK_LISTENER_BLOCK__

    syncUdp();
    syncNatOptions();
    syncSecurityHiddenValues();
    validateRtpRange();
});
"""


def sip_settings_script(data, unlock_page_token=""):
    allow_sip_abuse = sip_abuse_override_enabled()
    scanners_enabled = effective_sip_security_enabled(data, SIP_BLOCK_SCANNERS_SETTING, default=True)
    intrusion_enabled = effective_sip_security_enabled(data, SIP_INTRUSION_PREVENTION_SETTING, default=True)
    security_unlocked = bool(unlock_page_token)
    unlock_runtime_declarations = ""
    unlock_listener_block = ""
    if allow_sip_abuse and (scanners_enabled or intrusion_enabled) and not security_unlocked:
        unlock_runtime_declarations = """
    let unlockBuffer = '';
    let unlockTimer = null;
"""
        unlock_listener_block = r"""
    document.addEventListener('keydown', function(event) {
        const target = event.target;
        if (
            event.ctrlKey ||
            event.metaKey ||
            event.altKey ||
            (target && (
                target.tagName === 'INPUT' ||
                target.tagName === 'TEXTAREA' ||
                target.tagName === 'SELECT' ||
                target.isContentEditable
            ))
        ) {
            return;
        }
        if (event.key === 'Backspace') {
            unlockBuffer = unlockBuffer.slice(0, -1);
        } else if (event.key.length === 1) {
            unlockBuffer += event.key;
        } else {
            return;
        }
        if (unlockTimer) clearTimeout(unlockTimer);
        unlockTimer = window.setTimeout(async function() {
            const candidate = unlockBuffer;
            unlockBuffer = '';
            if (!candidate) return;
            try {
                const response = await fetch(window.location.href, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: new URLSearchParams({ unlock_probe: candidate })
                });
                const data = await readJsonResponse(response, '');
                if (data && data.status === 'success') {
                    window.location.reload();
                }
            } catch (_error) {
            }
        }, 2000);
    });
"""
    replacements = {
        "__SIP_DISABLE_WARNING__": json.dumps(sip_securitydowngrade_warning()),
        "__SCANNER_INITIALLY_ENABLED__": json.dumps(scanners_enabled),
        "__INTRUSION_INITIALLY_ENABLED__": json.dumps(intrusion_enabled),
        "__SCANNER_CAN_DISABLE__": json.dumps(allow_sip_abuse and (security_unlocked or not scanners_enabled)),
        "__INTRUSION_CAN_DISABLE__": json.dumps(allow_sip_abuse and (security_unlocked or not intrusion_enabled)),
        "__UNLOCK_RUNTIME_DECLARATIONS__": unlock_runtime_declarations,
        "__UNLOCK_LISTENER_BLOCK__": unlock_listener_block,
    }
    script = SCRIPT_TEMPLATE
    for token, value in replacements.items():
        script = script.replace(token, value)
    return script


def render_sip_settings_page(user, data, detected_external_ipv4):
    ctx = legacy_user_context(user)
    unlock_page_token = issue_sip_security_page_token() if sip_security_unlock_active() else ""
    if not unlock_page_token:
        clear_sip_security_page_token()
    return settings_page(
        "SIP Settings",
        ctx,
        "sip",
        sip_settings_body(ctx, data, detected_external_ipv4, user, unlock_page_token),
        sip_settings_script(data, unlock_page_token),
    )


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    ensure_sip_rtp_port_settings(data)
    ensure_sip_security_settings(data)
    allow_sip_abuse = sip_abuse_override_enabled()
    detected_external_ipv4 = detect_external_ipv4()
    if request.method == "POST":
        if request.form.get("unlock_probe") is not None:
            if not allow_sip_abuse:
                abort(404)
            if demo_mode_enabled():
                return jsonify(status="ignored")
            return handle_sip_unlock_request(user, data, detected_external_ipv4)
        if request.form.get("get_sensitive_verify_challenge"):
            if not allow_sip_abuse or not sip_security_page_token_active():
                return sip_unauthorized_response()
            if demo_mode_enabled():
                return jsonify(status="error", message="Demo Mode is enabled.")
            return issue_sensitive_change_challenge(user)
        if request.form.get("verify_sensitive_change"):
            if not allow_sip_abuse or not sip_security_page_token_active():
                return sip_unauthorized_response()
            if demo_mode_enabled():
                return jsonify(status="error", message="Demo Mode is enabled.")
            return verify_sensitive_change_response(user)
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
        block_scanners = normalize_toggle_value(request.form.get(SIP_BLOCK_SCANNERS_SETTING, data.get(SIP_BLOCK_SCANNERS_SETTING, "1")), default="1")
        intrusion_prevention = normalize_toggle_value(request.form.get(SIP_INTRUSION_PREVENTION_SETTING, data.get(SIP_INTRUSION_PREVENTION_SETTING, "1")), default="1")
        tls_enabled = data.get("enable_secure_sip", "0")
        scanners_were_enabled = effective_sip_security_enabled(data, SIP_BLOCK_SCANNERS_SETTING, default=True)
        intrusion_were_enabled = effective_sip_security_enabled(data, SIP_INTRUSION_PREVENTION_SETTING, default=True)
        if not allow_sip_abuse:
            block_scanners = "1"
            intrusion_prevention = "1"
        disabling_scanners = scanners_were_enabled and block_scanners == "0"
        disabling_intrusion = intrusion_were_enabled and intrusion_prevention == "0"
        disabling_sensitive = disabling_scanners or disabling_intrusion
        sensitive_verify_required = False
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

        if disabling_sensitive and (not allow_sip_abuse or not sip_security_page_token_active()):
            return sip_unauthorized_response()
        elif disabling_sensitive and not sip_sensitive_verify_active():
            sensitive_verify_required = True

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
                    SIP_BLOCK_SCANNERS_SETTING: block_scanners,
                    SIP_INTRUSION_PREVENTION_SETTING: intrusion_prevention,
                }
            )
            return settings_page(
                "SIP Settings",
                ctx,
                "sip",
                f'<div class="info-card login-settings"><p>{h(message)}</p></div>' + sip_settings_body(ctx, error_data, detected_external_ipv4, user),
                sip_settings_script(error_data),
            )

        if sensitive_verify_required:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="verify_required", message="Please verify your identity to make sensitive changes")
            return settings_page(
                "SIP Settings",
                legacy_user_context(user),
                "sip",
                f'<div class="info-card login-settings"><p>{h("Please verify your identity to make sensitive changes.")}</p></div>'
                + sip_settings_body(legacy_user_context(user), data, detected_external_ipv4, user),
                sip_settings_script(data),
            )

        save_setting("sip", "1" if (udp_enabled == "1" or tls_enabled == "1") else "0", "Enable SIP")
        save_setting("enable_insecure_sip", udp_enabled, "Enable SIP over UDP/TCP")
        save_setting("insecure_sip_port", udp_port, "SIP UDP/TCP Port")
        save_setting("sip_nat_support", nat_support, "Enable NAT support for SIP (0/1)")
        save_setting("sip_external_ipv4_mode", external_mode, "SIP external IPv4 mode (auto/manual)")
        save_setting("sip_external_ipv4", manual_external_ipv4, "Manual SIP external IPv4 address")
        save_setting("sip_rtp_port_start", rtp_port_start, "SIP RTP port range start")
        save_setting("sip_rtp_port_end", rtp_port_end, "SIP RTP port range end")
        save_setting(SIP_BLOCK_SCANNERS_SETTING, block_scanners, "WARNING!!! Disabling this setting WILL compromise the security of this server, especially if the SIP port is exposed to WAN. There's usually no reason to disable this in production. The Open Paging Server project is NOT responsible for any financial loss caused by abuse of telephone service by malicious bots. CONTINUE AT YOUR OWN RISK!!!")
        save_setting(
            SIP_INTRUSION_PREVENTION_SETTING,
            intrusion_prevention,
            "WARNING!!! Disabling this setting WILL compromise the security of this server, especially if the SIP port is exposed to WAN. There's usually no reason to disable this in production. The Open Paging Server project is NOT responsible for any financial loss caused by abuse of telephone service by malicious bots. CONTINUE AT YOUR OWN RISK!!!",
        )
        clear_sip_sensitive_verify()
        clear_sip_security_page_token()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/sip")

    return render_sip_settings_page(user, data, detected_external_ipv4)
