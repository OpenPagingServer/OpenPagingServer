from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page
def _select_option_html(current, value, label):
    return f'<option value="{h(value)}"{" selected" if str(current) == str(value) else ""}>{h(label)}</option>'


def _normalize_login_settings(form, existing):
    cleaned = {
        "login_banner_enabled": "1" if form.get("login_banner_enabled") else "0",
        "login_banner_title": str(form.get("login_banner_title") or ""),
        "login_banner_message": str(form.get("login_banner_message") or ""),
        "login_captcha_provider": str(form.get("login_captcha_provider") or "disabled").strip().lower(),
        "login_captcha_site_key": str(form.get("login_captcha_site_key") or "").strip(),
        "login_captcha_secret_key": "",
        "login_captcha_external_only": "1" if form.get("login_captcha_external_only") else "0",
        ACCOUNT_EXPIRATION_NOTIFY_SETTING: "1" if form.get(ACCOUNT_EXPIRATION_NOTIFY_SETTING) else "0",
        GUEST_RECEIVER_SETTING: "1" if form.get(GUEST_RECEIVER_SETTING) else "0",
    }
    errors = []
    valid_providers = {"disabled", "basic", "turnstile", "recaptcha"}
    if cleaned["login_captcha_provider"] not in valid_providers:
        errors.append("Select a valid CAPTCHA provider.")
        cleaned["login_captcha_provider"] = "disabled"
    existing_secret = str(existing.get("login_captcha_secret_key") or "").strip()
    cleaned["login_captcha_secret_key"] = (
        str(form.get("login_captcha_secret_key") or "").strip() or existing_secret
    ) if cleaned["login_captcha_provider"] in {"turnstile", "recaptcha"} else ""
    if cleaned["login_captcha_provider"] in {"turnstile", "recaptcha"} and (
        not cleaned["login_captcha_site_key"] or not cleaned["login_captcha_secret_key"]
    ):
        errors.append("Site key and secret key are required for the selected CAPTCHA provider.")
    return cleaned, errors


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    if request.method == "POST":
        if demo_mode_enabled():
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="error", message="Demo Mode is enabled."), 403
            return demo_mode_page("Login Settings", legacy_user_context(user), "settings", "settings")
        cleaned, errors = _normalize_login_settings(request.form, data)
        if errors:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="error", message=" ".join(errors))
            return page("Login Settings", h(" ".join(errors)), "settings", user)
        save_setting("login_banner_enabled", cleaned["login_banner_enabled"], "Enable login banner")
        save_setting("login_banner_title", cleaned["login_banner_title"], "Login banner title")
        save_setting("login_banner_message", cleaned["login_banner_message"], "Login banner message")
        save_setting("login_captcha_provider", cleaned["login_captcha_provider"], "Login CAPTCHA provider")
        save_setting("login_captcha_site_key", cleaned["login_captcha_site_key"], "Login CAPTCHA site key")
        save_setting("login_captcha_secret_key", cleaned["login_captcha_secret_key"], "Login CAPTCHA secret key")
        save_setting("login_captcha_external_only", cleaned["login_captcha_external_only"], "Require login CAPTCHA only for external IP addresses (0/1)")
        save_setting(ACCOUNT_EXPIRATION_NOTIFY_SETTING, cleaned[ACCOUNT_EXPIRATION_NOTIFY_SETTING], "Notify users whose accounts are about to expire (0/1)")
        save_setting(GUEST_RECEIVER_SETTING, cleaned[GUEST_RECEIVER_SETTING], "Enable guest receiver (0/1)")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/login")

    ctx = legacy_user_context(user)
    banner_enabled = data.get("login_banner_enabled", "0") == "1"
    banner_disabled = "" if banner_enabled else " disabled"
    captcha_provider = str(data.get("login_captcha_provider") or "disabled").strip().lower()
    if captcha_provider not in {"disabled", "basic", "turnstile", "recaptcha"}:
        captcha_provider = "disabled"
    captcha_options = "".join(
        _select_option_html(captcha_provider, value, label)
        for value, label in (
            ("disabled", "Disabled"),
            ("basic", "Basic Captcha"),
            ("turnstile", "Cloudflare Turnstile"),
            ("recaptcha", "Google reCAPTCHA"),
        )
    )
    body = f"""
    <style>
        .settings-section-divider {{
            border-bottom: 1px solid #333;
            padding-bottom: 16px;
            margin-bottom: 20px;
        }}
    </style>
    <div id="login" class="tab-content active">
        <div class="info-card login-settings">
            <form id="loginSettingsForm">
                <h4 style="margin-top:20px;">Login Banner</h4>
                <p>Show an optional message before users sign in.</p>
                <div class="info-row" style="border-bottom: none;">
                    <span class="info-label">Enable Banner</span>
                    <span><label class="switch"><input type="checkbox" name="login_banner_enabled" id="bannerToggle"{" checked" if banner_enabled else ""}><span class="slider"></span></label></span>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom: none;">
                    <span class="info-label">Title</span>
                    <input type="text" name="login_banner_title" id="bannerTitle" value="{h(data.get("login_banner_title", ""))}"{banner_disabled}>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                    <span class="info-label">Message</span>
                    <textarea name="login_banner_message" id="bannerMessage"{banner_disabled}>{h(data.get("login_banner_message", ""))}</textarea>
                </div>
                <h4 style="margin-top:20px;">CAPTCHA</h4>
                <p>Enabling CAPTCHA is highly recommended if you are making the web interface public to protect your server from automated login attempts. However, it's not a replacement for other security measures.</p>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom: none;">
                    <span class="info-label">Provider</span>
                    <select name="login_captcha_provider" id="captchaProvider">
                        {captcha_options}
                    </select>
                    <span id="captchaProviderHint" class="info-description"></span>
                </div>
                <div class="info-row captcha-key-row" id="captchaSiteKeyRow" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom: none;">
                    <span class="info-label">Site Key</span>
                    <input type="text" name="login_captcha_site_key" id="captchaSiteKey" value="{h(data.get("login_captcha_site_key", ""))}" autocomplete="off">
                </div>
                <div class="info-row captcha-key-row" id="captchaSecretKeyRow" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom: none;">
                    <span class="info-label">Secret Key</span>
                    <input type="password" name="login_captcha_secret_key" id="captchaSecretKey" value="" autocomplete="off" placeholder="Leave blank to keep current secret key">
                </div>
                <div class="info-row" style="border-bottom: none;">
                    <span class="info-label">
                        Require CAPTCHA only for external IP addresses
                        <span class="info-description">Enabled by default. Private, loopback, and other non-public client IPs skip CAPTCHA.</span>
                    </span>
                    <span><label class="switch"><input type="checkbox" name="login_captcha_external_only" id="captchaExternalOnly"{" checked" if data.get("login_captcha_external_only", "1") == "1" else ""}><span class="slider"></span></label></span>
                </div>
                <div class="settings-section-divider" style="margin-top: 20px; border-bottom: none; padding-bottom: 0; border-top: 1px solid #333; padding-top: 16px;">
                    <div class="info-row" style="border-bottom: none;">
                        <div class="info-label"><h4 style="margin:0;">Notify users whose accounts are about to expire</h4></div>
                        <span><label class="switch"><input type="checkbox" name="{ACCOUNT_EXPIRATION_NOTIFY_SETTING}" id="accountExpirationNotifyToggle"{" checked" if data.get(ACCOUNT_EXPIRATION_NOTIFY_SETTING, "1") == "1" else ""}><span class="slider"></span></label></span>
                    </div>
                </div>
                <div class="settings-section-divider" style="border-bottom: none; padding-bottom: 0; border-top: 1px solid #333; padding-top: 16px;">
                    <div class="info-row" style="border-bottom: none;">
                        <div class="info-label">
                            <h4 style="margin:0;">Enable guest receiver</h4>
                            <span class="info-description">When enabled, a new recipient becomes available which allows logged out users to receive messages. Anything sent to this recipient will be able to be seen by anyone able to access the web interface.</span>
                        </div>
                        <span><label class="switch"><input type="checkbox" name="{GUEST_RECEIVER_SETTING}" id="guestReceiverToggle"{" checked" if data.get(GUEST_RECEIVER_SETTING, "0") == "1" else ""}><span class="slider"></span></label></span>
                    </div>
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
    const captchaProvider = document.getElementById('captchaProvider');
    const captchaSiteKeyRow = document.getElementById('captchaSiteKeyRow');
    const captchaSecretKeyRow = document.getElementById('captchaSecretKeyRow');
    const captchaProviderHint = document.getElementById('captchaProviderHint');
    if (bannerToggle) {
        bannerToggle.addEventListener('change', function() {
            bannerTitle.disabled = !this.checked;
            bannerMessage.disabled = !this.checked;
        });
    }

    function syncCaptchaFields() {
        if (!captchaProvider) return;
        const value = captchaProvider.value;
        const usesExternalKeys = value === 'turnstile' || value === 'recaptcha';
        if (captchaSiteKeyRow) captchaSiteKeyRow.style.display = usesExternalKeys ? 'flex' : 'none';
        if (captchaSecretKeyRow) captchaSecretKeyRow.style.display = usesExternalKeys ? 'flex' : 'none';
        if (captchaProviderHint) {
            if (value === 'basic') {
                captchaProviderHint.innerText = 'The server generates a local image challenge.';
            } else if (value === 'turnstile') {
                captchaProviderHint.innerText = 'Enter the Cloudflare Turnstile site key and secret key.';
            } else if (value === 'recaptcha') {
                captchaProviderHint.innerText = 'Enter the Google reCAPTCHA site key and secret key.';
            } else {
                captchaProviderHint.innerText = 'No CAPTCHA will be shown on the login page.';
            }
        }
    }

    if (captchaProvider) {
        captchaProvider.addEventListener('change', syncCaptchaFields);
        syncCaptchaFields();
    }
    postSettings('loginSettingsForm','saveLoginBtn','save-status','Settings saved successfully.', false);
});
"""
    return settings_page("Login Settings", ctx, "login", body, script)
