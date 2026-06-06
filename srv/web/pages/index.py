import hmac
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from srv.web.app import *

CAPTCHA_PROVIDERS = {"basic", "turnstile", "recaptcha"}
CAPTCHA_DISABLED_VALUES = {"", "disabled", "none", "off", "0", "false"}
CAPTCHA_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
CAPTCHA_RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"
BASIC_CAPTCHA_SESSION_KEY = "login_basic_captcha_hash"
BASIC_CAPTCHA_EXPIRES_KEY = "login_basic_captcha_expires"
BASIC_CAPTCHA_TTL_SECONDS = 300


def _setting_bool(data, key):
    return str(data.get(key, "0")) == "1"


def _success_value(value):
    if isinstance(value, bool):
        return value
    try:
        return int(value) == 1
    except Exception:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _attempt_time_value(value):
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value).split(".", 1)[0], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _consecutive_failed_attempts(ip):
    rows = query_all(
        "SELECT success FROM login_attempts WHERE ip=%s ORDER BY attempt_time DESC LIMIT 200",
        (ip,),
    )
    count = 0
    for row in rows:
        if _success_value(row.get("success")):
            break
        count += 1
    return count


def _failed_delay_seconds(ip):
    return min(_consecutive_failed_attempts(ip) * 0.5, 10)


def _sleep_before_login_check(ip):
    time.sleep(1 + _failed_delay_seconds(ip))


def _recent_failed_attempt_times(ip):
    cutoff = datetime.now() - timedelta(seconds=10 + 30)
    rows = query_all(
        "SELECT success, attempt_time FROM login_attempts WHERE ip=%s AND attempt_time >= %s ORDER BY attempt_time ASC",
        (ip, cutoff),
    )
    times = []
    for row in rows:
        if _success_value(row.get("success")):
            continue
        attempt_time = _attempt_time_value(row.get("attempt_time"))
        if attempt_time is not None:
            times.append(attempt_time)
    return times


def _ip_rate_limited(ip):
    now = datetime.now()
    failed_times = _recent_failed_attempt_times(ip)
    for latest in failed_times:
        window_start = latest - timedelta(seconds=10)
        count = sum(1 for attempt_time in failed_times if window_start <= attempt_time <= latest)
        if count >= 3 and now < latest + timedelta(seconds=30):
            return True
    return False


def _fake_salt(username):
    return hashlib.sha256(("missing-user:" + username + ":" + app.secret_key).encode()).hexdigest()


def _record_login_attempt(ip, username, success, user_agent):
    execute(
        "INSERT INTO login_attempts (ip, username, success, attempt_time, user_agent) VALUES (%s,%s,%s,NOW(),%s)",
        (ip, username, 1 if success else 0, user_agent),
    )


def _invalid_login_response():
    return jsonify(success=False, message="Invalid username or password.")


def _captcha_failed_response():
    return jsonify(success=False, message="CAPTCHA verification failed.")


def _captcha_provider(data):
    provider = str((data or {}).get("login_captcha_provider") or "disabled").strip().lower()
    if provider in CAPTCHA_DISABLED_VALUES:
        return ""
    return provider if provider in CAPTCHA_PROVIDERS else ""


def _captcha_site_key(data):
    return str((data or {}).get("login_captcha_site_key") or "").strip()


def _captcha_secret_key(data):
    return str((data or {}).get("login_captcha_secret_key") or "").strip()


def _configured_captcha_provider(data):
    provider = _captcha_provider(data)
    if provider in {"turnstile", "recaptcha"} and not (_captcha_site_key(data) and _captcha_secret_key(data)):
        return ""
    return provider


def _basic_captcha_hash(value):
    normalized = str(value or "").strip().lower()
    return hashlib.sha256((normalized + "|" + app.secret_key).encode()).hexdigest()


def _verify_basic_captcha(value):
    expected = session.pop(BASIC_CAPTCHA_SESSION_KEY, "")
    expires_raw = session.pop(BASIC_CAPTCHA_EXPIRES_KEY, "0")
    try:
        expires_at = float(expires_raw)
    except (TypeError, ValueError):
        expires_at = 0
    if not expected or time.time() > expires_at:
        return False
    return hmac.compare_digest(str(expected), _basic_captcha_hash(value))


def _siteverify(url, secret, token, ip):
    payload = {
        "secret": secret,
        "response": token,
    }
    if ip:
        payload["remoteip"] = ip
    request_body = urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=request_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "OpenPagingServer/0.3.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8", errors="replace"))
        return bool(result.get("success"))
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
        return False


def _verify_captcha(data, ip):
    provider = _configured_captcha_provider(data)
    if not provider:
        return True
    token = (
        request.form.get("captcha_response")
        or request.form.get("cf-turnstile-response")
        or request.form.get("g-recaptcha-response")
        or ""
    ).strip()
    if not token:
        return False
    if provider == "basic":
        return _verify_basic_captcha(token)
    if provider == "turnstile":
        return _siteverify(CAPTCHA_TURNSTILE_VERIFY_URL, _captcha_secret_key(data), token, ip)
    if provider == "recaptcha":
        return _siteverify(CAPTCHA_RECAPTCHA_VERIFY_URL, _captcha_secret_key(data), token, ip)
    return True


def _captcha_markup(provider, site_key):
    if provider == "basic":
        return """
        <div class="captcha-section" data-provider="basic">
          <div class="basic-captcha-row">
            <img id="basic-captcha-image" src="/login/basic-captcha.svg" alt="CAPTCHA image" />
            <button type="button" class="basic-captcha-refresh" onclick="refreshBasicCaptcha()" aria-label="Refresh CAPTCHA"><i class="fa-solid fa-rotate-right"></i></button>
          </div>
          <div class="input-field captcha-answer-field">
            <input type="text" id="captcha-basic-response" placeholder=" " autocomplete="off" required />
            <label for="captcha-basic-response">CAPTCHA Text</label>
          </div>
        </div>"""
    if provider == "turnstile":
        return f"""
        <div class="captcha-section" data-provider="turnstile">
          <div class="cf-turnstile" data-sitekey="{h(site_key)}"></div>
        </div>"""
    if provider == "recaptcha":
        return f"""
        <div class="captcha-section" data-provider="recaptcha">
          <div class="g-recaptcha" data-sitekey="{h(site_key)}"></div>
        </div>"""
    return ""


def _captcha_script_tag(provider):
    if provider == "turnstile":
        return '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>'
    if provider == "recaptcha":
        return '<script src="https://www.google.com/recaptcha/api.js" async defer></script>'
    return ""


def handle_request():
    login_error = ""
    try:
        ctx = product_context()
        data = ctx["settings"]
        if (BASE_DIR / ".oobe").is_file():
            row = query_one("SELECT COUNT(*) AS count FROM users")
            if row and int(row["count"]) == 0:
                return redirect("/oobe/")
    except Exception as exc:
        ctx = {"product_name": "Open Paging Server", "favicon": "", "settings": {}}
        data = {}
        login_error = "Initialization failed: " + str(exc)

    if session.get("user_id") is not None and session.get("user_id") != "":
        return redirect("/dashboard")

    if request.method == "POST":
        ip = request.remote_addr or ""
        ua = request.headers.get("User-Agent", "unknown")
        _sleep_before_login_check(ip)

        try:
            if _ip_rate_limited(ip):
                return _invalid_login_response()

            if "get_challenge" in request.form:
                username = request.form.get("username", "").strip()
                user = query_one("SELECT salt FROM users WHERE username=%s OR email=%s LIMIT 1", (username, username))
                challenge = secrets.token_hex(32)
                session["temp_challenge"] = challenge
                session["temp_user"] = username
                salt = user["salt"] if user and user.get("salt") is not None else _fake_salt(username)
                return jsonify(success=True, salt=salt, challenge=challenge)

            if "response" in request.form:
                if not _verify_captcha(data, ip):
                    session.pop("temp_challenge", None)
                    session.pop("temp_user", None)
                    return _captcha_failed_response()
                username = session.get("temp_user", "")
                challenge = session.get("temp_challenge", "")
                user = query_one("SELECT id, username, password FROM users WHERE username=%s OR email=%s LIMIT 1", (username, username))
                expected = hashlib.sha256(((user or {}).get("password", "") + challenge).encode()).hexdigest() if user and challenge else ""
                ok = bool(user and challenge and request.form.get("response") == expected)
                _record_login_attempt(ip, username, ok, ua)
                if ok:
                    session.clear()
                    session["user_id"] = user["id"]
                    session["username"] = user["username"]
                    return jsonify(success=True)
                session.pop("temp_challenge", None)
                session.pop("temp_user", None)
                return _invalid_login_response()

            return _invalid_login_response()
        except Exception:
            return _invalid_login_response()

    product_name = data.get("product_name") or "Open Paging Server"
    favicon = data.get("favicon") or ""
    banner_enabled = _setting_bool(data, "login_banner_enabled")
    banner_title = data.get("login_banner_title") or ""
    banner_message = data.get("login_banner_message") or ""
    separate_dark_logo = _setting_bool(data, "separate_dark_logo")
    enable_login_logo = _setting_bool(data, "enable_login_logo")
    login_logo_light = data.get("login_logo_light") or ""
    login_logo_dark = data.get("login_logo_dark") or ""
    captcha_provider = _configured_captcha_provider(data)
    captcha_site_key = _captcha_site_key(data)
    captcha_html = _captcha_markup(captcha_provider, captcha_site_key)
    captcha_script = _captcha_script_tag(captcha_provider)

    favicon_html = f'<link rel="icon" href="{h(favicon)}" type="image/x-icon">' if favicon else ""
    dark_logo_css = ".logo-light { display: none; }\n        .logo-dark { display: block; }" if separate_dark_logo else ""
    logo_html = ""
    if enable_login_logo:
        if separate_dark_logo:
            logo_html = f"""
    <div class="logo">
        <img src="{h(login_logo_light)}" alt="{h(product_name)} logo" class="logo-light" />
        <img src="{h(login_logo_dark)}" alt="{h(product_name)} logo" class="logo-dark" />
    </div>"""
        else:
            logo_html = f"""
    <div class="logo">
        <img src="{h(login_logo_light)}" alt="{h(product_name)} logo" />
    </div>"""

    banner_html = ""
    if banner_enabled and (banner_title or banner_message):
        title_html = f"<h3>{h(banner_title)}</h3>" if banner_title else ""
        message_html = f"<p>{h(banner_message).replace(chr(10), '<br>')}</p>" if banner_message else ""
        banner_html = f"""
        <div class="login-banner">
          {title_html}
          {message_html}
        </div>"""

    body = f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Login - {h(product_name)}</title>
    {favicon_html}
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet"/>
    <script src="https://cdn.jsdelivr.net/npm/js-sha256@0.9.0/src/sha256.min.js"></script>
    {captcha_script}
    <style>
      *, *::before, *::after {{ box-sizing: border-box; }}
      body, html {{ margin: 0; padding: 0; font-family: "Tahoma", sans-serif; height: 100%; width: 100%; position: fixed; display: flex; align-items: center; justify-content: center; background: #e3f2fd; overflow-x: hidden; }}
      @keyframes fadeInPage {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
      .background-slideshow {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; }}
      @media (max-width: 768px) {{ .background-slideshow {{ display: none; }} }}
      .center-container {{ display: flex; flex-direction: column; justify-content: center; align-items: center; width: 100%; height: 100%; position: relative; z-index: 1; }}
      .logo {{ position: fixed; top: 20px; left: 50%; transform: translateX(-50%); z-index: 2; width: 830px; height: 97px; display: flex; justify-content: center; align-items: center; }}
      .logo img {{ max-width: 100%; max-height: 100%; object-fit: contain; }}
      .logo-light {{ display: block; }}
      .logo-dark {{ display: none; }}
      @media (max-width: 768px) {{ .logo {{ position: relative; top: auto; left: auto; transform: none; width: min(82vw, 360px); height: auto; margin: 18px auto 12px auto; padding: 0; flex: 0 0 auto; }} .logo img {{ width: 100%; height: auto; max-height: 110px; }} }}
      @media (min-width: 769px) {{ .logo.logo-corner {{ top: 16px; left: 16px; transform: none; width: min(320px, 34vw); height: auto; justify-content: flex-start; }} .logo.logo-corner img {{ width: 100%; height: auto; max-height: 70px; object-fit: contain; object-position: left center; }} }}
      .login-banner {{ background: #fff3e0; border: 1px solid #ffe0b2; border-radius: 6px; padding: 15px; margin-bottom: 15px; width: 100%; max-width: 390px; box-sizing: border-box; text-align: left; color: #e65100; box-shadow: 0 2px 4px rgba(0,0,0,0.05); animation: fadeInPage 1s ease-in-out; }}
      .login-banner h3 {{ margin: 0 0 5px 0; font-size: 15px; font-weight: 700; text-transform: uppercase; }}
      .login-banner p {{ margin: 0; font-size: 14px; line-height: 1.4; }}
      .login-box {{ background: #fff; padding: 30px; border-radius: 6px; box-shadow: 0 4px 6px rgba(0,0,0,0.1),0 1px 3px rgba(0,0,0,0.08); max-width: 390px; width: min(92vw, 390px); text-align: center; animation: fadeInPage 1.5s ease-in-out; }}
      @media (max-width: 768px) {{ 
        body, html {{ position: static; height: auto; min-height: 100%; display: block; }}
        body {{ background: #fff; min-height: 100vh; overflow-y: auto; }}
        .center-container {{ width: 100%; height: auto; min-height: auto; padding: 0 16px 24px 16px; align-items: center; justify-content: flex-start; }}
        .login-box {{ max-width: 360px; width: 100%; height: auto; border-radius: 6px; padding: 22px; }} 
        .login-banner {{ max-width: 360px; width: 100%; border-radius: 4px; }}
      }}
      .login-box h2 {{ color: #1976d2; font-weight: 500; margin-bottom: 20px; margin-top: 0; }}
      .input-field {{ position: relative; margin-bottom: 20px; }}
      .input-field input {{ width: 100%; padding: 8px 0; border: none; border-bottom: 2px solid #ccc; font-size: 16px; background: transparent; outline: none; color: #333; font-family: "Roboto", sans-serif; }}
      .input-field input:focus {{ border-bottom: 2px solid #1976d2; }}
      .input-field label {{ position: absolute; top: 8px; left: 0; color: #888; font-size: 14px; pointer-events: none; transition: 0.2s ease all; }}
      .input-field input:focus ~ label, .input-field input:not(:placeholder-shown) ~ label {{ top: -16px; left: 0; font-size: 12px; color: #1976d2; }}
      .captcha-section {{ width: 100%; margin: 0 0 18px 0; display: flex; flex-direction: column; align-items: center; gap: 12px; }}
      .basic-captcha-row {{ display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; }}
      .basic-captcha-row img {{ width: 220px; max-width: calc(100% - 50px); height: 70px; border: 1px solid #ccc; border-radius: 4px; background: #f5f5f5; }}
      .login-box .basic-captcha-refresh {{ width: 42px; height: 42px; padding: 0; flex: 0 0 42px; border-radius: 4px; font-size: 16px; text-transform: none; }}
      .captcha-answer-field {{ width: 100%; margin-bottom: 0; }}
      .cf-turnstile, .g-recaptcha {{ max-width: 100%; }}
      .login-box button {{ width: 100%; padding: 12px; background-color: #1976d2; border: none; color: #fff; font-size: 16px; border-radius: 4px; cursor: pointer; font-family: "Roboto", sans-serif; text-transform: uppercase; position: relative; height: 45px; display: inline-flex; align-items: center; justify-content: center; }}
      .login-box button.loading {{ pointer-events: none; background-color: #1565c0; }}
      .loading-circle {{ width: 24px; height: 24px; border: 2px solid rgba(255,255,255,0.3); border-top: 2px solid #fff; border-radius: 50%; animation: spin 1s linear infinite; position: absolute; }}
      @keyframes spin {{ from {{ transform: rotate(0deg); }} to {{ transform: rotate(360deg); }} }}
      .error {{ color: #d32f2f; font-size: 0.9em; margin-top: 10px; min-height: 1.2em; }}
      @media (prefers-color-scheme: dark) {{
        body, html {{ background: #121212; color: #fff; }}
        .login-banner {{ background: #3e2723; border: 1px solid #5d4037; color: #ffb74d; }}
        .login-box {{ background: #1e1e1e; box-shadow: 0 4px 6px rgba(0,0,0,0.6); }}
        .login-box h2 {{ color: #fff; }}
        .input-field input {{ color: #fff; border-bottom: 2px solid #555; }}
        .input-field label {{ color: #ccc; }}
        .basic-captcha-row img {{ background: #2a2a2a; border-color: #555; }}
        .login-box button {{ background-color: #90caf9; color: #121212; }}
        .error {{ color: #ffcdd2; }}
        {dark_logo_css}
      }}
      @media (prefers-color-scheme: dark) and (max-width: 768px) {{ body {{ background: #121212; }} }}
    </style>
  </head>
  <body>
    <div class="background-slideshow"></div>
    {logo_html}
    <div class="center-container">
      {banner_html}
      <div class="login-box">
        <h2>Login</h2>
        <div class="input-field">
          <input type="text" id="username" placeholder=" " required />
          <label for="username">Username or Email</label>
        </div>
        <div class="input-field">
          <input type="password" id="pw" placeholder=" " required />
          <label for="pw">Password</label>
        </div>
        {captcha_html}
        <button id="login-button" onclick="startLogin()">Login</button>
        <p id="login-error" class="error">{h(login_error)}</p>
      </div>
    </div>
    <script>
      function rectsOverlap(a, b) {{
        return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
      }}

      function adjustLogoPosition() {{
        const logo = document.querySelector('.logo');
        if (!logo) return;

        if (window.innerWidth <= 768) {{
          logo.classList.remove('logo-corner');
          return;
        }}

        logo.classList.remove('logo-corner');

        requestAnimationFrame(() => {{
          const logoRect = logo.getBoundingClientRect();
          const targets = Array.from(document.querySelectorAll('.login-banner, .login-box'));
          const horizontallyClipped = logoRect.left < 8 || logoRect.right > window.innerWidth - 8;
          const overlaps = targets.some((target) => {{
            const targetRect = target.getBoundingClientRect();
            return rectsOverlap(logoRect, targetRect);
          }});

          if (horizontallyClipped || overlaps) {{
            logo.classList.add('logo-corner');
          }}
        }});
      }}

      window.addEventListener('load', adjustLogoPosition);
      window.addEventListener('resize', adjustLogoPosition);
      document.addEventListener('DOMContentLoaded', adjustLogoPosition);
      Array.from(document.images).forEach((img) => img.addEventListener('load', adjustLogoPosition));

      const captchaProvider = "{captcha_provider}";

      function refreshBasicCaptcha() {{
        const image = document.getElementById('basic-captcha-image');
        const input = document.getElementById('captcha-basic-response');
        if (image) image.src = '/login/basic-captcha.svg?ts=' + Date.now() + '-' + Math.random();
        if (input) input.value = '';
      }}

      function captchaPayload() {{
        if (!captchaProvider) return {{}};
        if (captchaProvider === 'basic') {{
          const input = document.getElementById('captcha-basic-response');
          const value = input ? input.value.trim() : '';
          if (!value) throw new Error('Enter the CAPTCHA text.');
          return {{ captcha_provider: captchaProvider, captcha_response: value }};
        }}
        const fieldName = captchaProvider === 'turnstile' ? 'cf-turnstile-response' : 'g-recaptcha-response';
        const field = document.querySelector('[name="' + fieldName + '"]');
        const token = field ? field.value.trim() : '';
        if (!token) throw new Error('Complete the CAPTCHA.');
        return {{ captcha_provider: captchaProvider, captcha_response: token }};
      }}

      function resetCaptcha() {{
        if (captchaProvider === 'basic') {{
          refreshBasicCaptcha();
        }} else if (captchaProvider === 'turnstile' && window.turnstile && typeof window.turnstile.reset === 'function') {{
          window.turnstile.reset();
        }} else if (captchaProvider === 'recaptcha' && window.grecaptcha && typeof window.grecaptcha.reset === 'function') {{
          window.grecaptcha.reset();
        }}
      }}

      async function startLogin() {{
        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('pw').value;
        const btn = document.getElementById('login-button');
        const err = document.getElementById('login-error');

        if (!username || !password) {{
          err.innerText = 'Enter username and password';
          return;
        }}

        let captchaFields = {{}};
        try {{
          captchaFields = captchaPayload();
        }} catch (e) {{
          err.innerText = e.message || 'Complete the CAPTCHA.';
          return;
        }}

        err.innerText = '';
        btn.classList.add('loading');
        btn.innerHTML = '<div class="loading-circle"></div>';

        try {{
          const res1 = await fetch('/', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
            body: new URLSearchParams({{ get_challenge: 1, username: username }})
          }});

          const data1 = await res1.json();

          if (!data1.success) throw new Error(data1.message || 'Invalid username or password.');

          const verifier = sha256(password + data1.salt);
          const proof = sha256(verifier + data1.challenge);

          const proofPayload = new URLSearchParams({{ response: proof }});
          Object.entries(captchaFields).forEach(([key, value]) => proofPayload.append(key, value));

          const res2 = await fetch('/', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
            body: proofPayload
          }});

          const data2 = await res2.json();

          if (data2.success) {{
            window.location.href = '/dashboard';
          }} else {{
            throw new Error(data2.message || 'Invalid username or password.');
          }}
        }} catch (e) {{
          err.innerText = e.message || 'Invalid username or password.';
          resetCaptcha();
          btn.classList.remove('loading');
          btn.innerHTML = 'Login';
        }}
      }}
    </script>
  </body>
</html>"""
    return Response(body, mimetype="text/html")
