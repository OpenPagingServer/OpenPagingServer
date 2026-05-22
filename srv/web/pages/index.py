
from srv.web.app import *


def _setting_bool(data, key):
    return str(data.get(key, "0")) == "1"


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

    if session.get("user_id"):
        return redirect("/dashboard")

    if request.method == "POST":
        ip = request.remote_addr or ""
        ua = request.headers.get("User-Agent", "unknown")

        if "get_challenge" in request.form:
            username = request.form.get("username", "").strip()
            try:
                user = query_one("SELECT salt FROM users WHERE username=%s OR email=%s LIMIT 1", (username, username))
                execute(
                    "INSERT INTO login_attempts (ip, username, success, attempt_time, user_agent) VALUES (%s,%s,0,NOW(),%s)",
                    (ip, username, ua),
                )
                if not user:
                    return jsonify(success=False, message="Invalid username or password.")
                challenge = secrets.token_hex(32)
                session["temp_challenge"] = challenge
                session["temp_user"] = username
                return jsonify(success=True, salt=user["salt"], challenge=challenge)
            except Exception as exc:
                return jsonify(success=False, message=str(exc))

        if "response" in request.form:
            try:
                username = session.get("temp_user", "")
                challenge = session.get("temp_challenge", "")
                user = query_one("SELECT id, username, password FROM users WHERE username=%s OR email=%s LIMIT 1", (username, username))
                expected = hashlib.sha256(((user or {}).get("password", "") + challenge).encode()).hexdigest() if user and challenge else ""
                ok = bool(user and challenge and request.form.get("response") == expected)
                execute(
                    "INSERT INTO login_attempts (ip, username, success, attempt_time, user_agent) VALUES (%s,%s,%s,NOW(),%s)",
                    (ip, username, 1 if ok else 0, ua),
                )
                if ok:
                    session.clear()
                    session["user_id"] = user["id"]
                    session["username"] = user["username"]
                    return jsonify(success=True)
                return jsonify(success=False, message="Authentication failed.")
            except Exception as exc:
                return jsonify(success=False, message=str(exc))

    product_name = data.get("product_name") or "Open Paging Server"
    favicon = data.get("favicon") or ""
    banner_enabled = _setting_bool(data, "login_banner_enabled")
    banner_title = data.get("login_banner_title") or ""
    banner_message = data.get("login_banner_message") or ""
    separate_dark_logo = _setting_bool(data, "separate_dark_logo")
    enable_login_logo = _setting_bool(data, "enable_login_logo")
    login_logo_light = data.get("login_logo_light") or ""
    login_logo_dark = data.get("login_logo_dark") or ""

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
    <style>
      body, html {{ margin: 0; padding: 0; font-family: "Tahoma", sans-serif; height: 100%; width: 100%; position: fixed; display: flex; align-items: center; justify-content: center; background: #e3f2fd; }}
      @keyframes fadeInPage {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
      .background-slideshow {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; }}
      @media (max-width: 768px) {{ .background-slideshow {{ display: none; }} }}
      .center-container {{ display: flex; flex-direction: column; justify-content: center; align-items: center; width: 100%; height: 100%; position: relative; z-index: 1; }}
      .logo {{ position: fixed; top: 20px; left: 50%; transform: translateX(-50%); z-index: 2; width: 830px; height: 97px; display: flex; justify-content: center; align-items: center; }}
      .logo img {{ max-width: 100%; max-height: 100%; object-fit: contain; }}
      .logo-light {{ display: block; }}
      .logo-dark {{ display: none; }}
      @media (max-width: 768px) {{ .logo {{ width: 80%; height: auto; top: 10px; padding: 10px; }} .logo img {{ width: 100%; height: auto; }} }}
      .login-banner {{ background: #fff3e0; border: 1px solid #ffe0b2; border-radius: 6px; padding: 15px; margin-bottom: 15px; width: 100%; max-width: 300px; box-sizing: border-box; text-align: left; color: #e65100; box-shadow: 0 2px 4px rgba(0,0,0,0.05); animation: fadeInPage 1s ease-in-out; }}
      .login-banner h3 {{ margin: 0 0 5px 0; font-size: 15px; font-weight: 700; text-transform: uppercase; }}
      .login-banner p {{ margin: 0; font-size: 14px; line-height: 1.4; }}
      .login-box {{ background: #fff; padding: 30px; border-radius: 6px; box-shadow: 0 4px 6px rgba(0,0,0,0.1),0 1px 3px rgba(0,0,0,0.08); max-width: 300px; width: 50%; text-align: center; animation: fadeInPage 1.5s ease-in-out; }}
      @media (max-width: 768px) {{ 
        .login-box {{ max-width: 100%; width: 100%; height: auto; border-radius: 0; padding: 20px; }} 
        .login-banner {{ max-width: 90%; border-radius: 4px; }}
        .center-container {{ padding: 0; align-items: center; }} 
        body {{ background: #fff; }} 
      }}
      .login-box h2 {{ color: #1976d2; font-weight: 500; margin-bottom: 20px; margin-top: 0; }}
      .input-field {{ position: relative; margin-bottom: 20px; }}
      .input-field input {{ width: 100%; padding: 8px 0; border: none; border-bottom: 2px solid #ccc; font-size: 16px; background: transparent; outline: none; color: #333; font-family: "Roboto", sans-serif; }}
      .input-field input:focus {{ border-bottom: 2px solid #1976d2; }}
      .input-field label {{ position: absolute; top: 8px; left: 0; color: #888; font-size: 14px; pointer-events: none; transition: 0.2s ease all; }}
      .input-field input:focus ~ label, .input-field input:not(:placeholder-shown) ~ label {{ top: -16px; left: 0; font-size: 12px; color: #1976d2; }}
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
        .login-box button {{ background-color: #90caf9; color: #121212; }}
        .error {{ color: #ffcdd2; }}
        {dark_logo_css}
      }}
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
        <button id="login-button" onclick="startLogin()">Login</button>
        <p id="login-error" class="error">{h(login_error)}</p>
      </div>
    </div>
    <script>
      async function startLogin() {{
        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('pw').value;
        const btn = document.getElementById('login-button');
        const err = document.getElementById('login-error');

        if (!username || !password) {{
          err.innerText = 'Enter username and password';
          return;
        }}

        err.innerText = '';
        btn.classList.add('loading');
        btn.innerHTML = '<div class="loading-circle"></div>';

        try {{
          const res1 = await fetch('/index', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
            body: new URLSearchParams({{ get_challenge: 1, username: username }})
          }});

          const data1 = await res1.json();

          if (!data1.success) throw new Error(data1.message);

          const verifier = sha256(password + data1.salt);
          const proof = sha256(verifier + data1.challenge);

          const res2 = await fetch('/index', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
            body: new URLSearchParams({{ response: proof }})
          }});

          const data2 = await res2.json();

          if (data2.success) {{
            window.location.href = '/dashboard';
          }} else {{
            throw new Error(data2.message || 'Verification failed');
          }}
        }} catch (e) {{
          err.innerText = e.message;
          btn.classList.remove('loading');
          btn.innerHTML = 'Login';
        }}
      }}
    </script>
  </body>
</html>"""
    return Response(body, mimetype="text/html")
