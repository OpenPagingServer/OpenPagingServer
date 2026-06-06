
from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    if request.method == "POST":
        captcha_provider = str(request.form.get("login_captcha_provider") or "disabled").strip().lower()
        captcha_site_key = str(request.form.get("login_captcha_site_key") or "").strip()
        captcha_secret_key = str(request.form.get("login_captcha_secret_key") or "").strip()
        valid_providers = {"disabled", "basic", "turnstile", "recaptcha"}
        errors = []
        if captcha_provider not in valid_providers:
            errors.append("Select a valid CAPTCHA provider.")
            captcha_provider = "disabled"
        if captcha_provider in {"turnstile", "recaptcha"} and (not captcha_site_key or not captcha_secret_key):
            errors.append("Site key and secret key are required for the selected CAPTCHA provider.")
        if errors:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="error", message=" ".join(errors))
            return page("Login Settings", h(" ".join(errors)), "settings", user)
        save_setting("login_banner_enabled", "1" if request.form.get("login_banner_enabled") else "0", "Enable login banner")
        save_setting("login_banner_title", request.form.get("login_banner_title", ""), "Login banner title")
        save_setting("login_banner_message", request.form.get("login_banner_message", ""), "Login banner message")
        save_setting("login_captcha_provider", captcha_provider, "Login CAPTCHA provider")
        save_setting("login_captcha_site_key", captcha_site_key, "Login CAPTCHA site key")
        save_setting("login_captcha_secret_key", captcha_secret_key, "Login CAPTCHA secret key")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/login")
    ctx = legacy_user_context(user)
    enabled = data.get("login_banner_enabled", "0") == "1"
    checked = " checked" if enabled else ""
    disabled = "" if enabled else " disabled"
    captcha_provider = str(data.get("login_captcha_provider") or "disabled").strip().lower()
    if captcha_provider not in {"disabled", "basic", "turnstile", "recaptcha"}:
        captcha_provider = "disabled"

    def option(value, label):
        selected = " selected" if captcha_provider == value else ""
        return f'<option value="{h(value)}"{selected}>{h(label)}</option>'

    body = f"""
    <div id="login" class="tab-content active">
        <div class="info-card login-settings">
            <form id="loginSettingsForm">
                <h4>Login Banner</h4>
                <p>Show an optional message before users sign in.</p>
                <div class="info-row">
                    <span class="info-label">Enable Banner</span>
                    <span><label class="switch"><input type="checkbox" name="login_banner_enabled" id="bannerToggle"{checked}><span class="slider"></span></label></span>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                    <span class="info-label">Title</span>
                    <input type="text" name="login_banner_title" id="bannerTitle" value="{h(data.get("login_banner_title", ""))}"{disabled}>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                    <span class="info-label">Message</span>
                    <textarea name="login_banner_message" id="bannerMessage"{disabled}>{h(data.get("login_banner_message", ""))}</textarea>
                </div>
                <h4 style="margin-top:20px;">CAPTCHA</h4>
                <p>Enabling CAPTCHA is highly recommended if you are making the web interface public to protect your server from automated login attempts. However, it's not a replacement for other security measures.</p>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                    <span class="info-label">Provider</span>
                    <select name="login_captcha_provider" id="captchaProvider">
                        {option("disabled", "Disabled")}
                        {option("basic", "Basic Captcha")}
                        {option("turnstile", "Cloudflare Turnstile")}
                        {option("recaptcha", "Google reCAPTCHA")}
                    </select>
                    <span id="captchaProviderHint" class="info-description"></span>
                </div>
                <div class="info-row captcha-key-row" id="captchaSiteKeyRow" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                    <span class="info-label">Site Key</span>
                    <input type="text" name="login_captcha_site_key" id="captchaSiteKey" value="{h(data.get("login_captcha_site_key", ""))}" autocomplete="off">
                </div>
                <div class="info-row captcha-key-row" id="captchaSecretKeyRow" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                    <span class="info-label">Secret Key</span>
                    <input type="password" name="login_captcha_secret_key" id="captchaSecretKey" value="{h(data.get("login_captcha_secret_key", ""))}" autocomplete="off">
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
