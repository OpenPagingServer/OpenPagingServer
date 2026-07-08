from srv.web.app import *


def session_agent_label(row):
    session_type = str((row or {}).get("session_type") or "web").strip().lower()
    agent = str((row or {}).get("user_agent") or "").strip()
    if session_type != "desktop":
        return agent or "Unknown"
    lowered = agent.lower()
    if "windows" in lowered:
        os_name = "Windows"
    elif "mac os" in lowered or "macos" in lowered or "macintosh" in lowered or "darwin" in lowered:
        os_name = "macOS"
    elif "linux" in lowered:
        os_name = "Linux"
    else:
        os_name = "Unknown OS"
    return f"Desktop Client - {os_name}"


USER_SETTINGS_STYLE = r"""
body, html { margin:0; padding:0; font-family:"Tahoma",sans-serif; font-weight:300; background-color:#FFF; height:100%; }
#sidebar { width:220px; background-color:#1976D2; color:#FFF; height:100vh; position:fixed; top:0; left:0; display:flex; flex-direction:column; box-shadow:2px 0 8px rgba(0,0,0,0.2); transition:transform 0.3s ease; z-index:1200; }
@media (max-width:767px){ #sidebar{ transform:translateX(-100%); } #sidebar.open{ transform:translateX(0); } }
#sidebar h2 { text-align:center; padding:20px 0; margin:0; font-weight:500; background-color:#1565C0; font-size:1.2em; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#FFF; padding:12px 20px; display:block; border-bottom:1px solid rgba(255,255,255,0.1); text-decoration:none; transition:background 0.3s; font-size:0.9em; text-align:left; box-sizing:border-box; }
#sidebar a i,.logout-btn i,.logout-btn-mobile i,.admin-only i { margin-right:8px; width:20px; }
#sidebar a:hover,#sidebar a.active{ background-color:#1565C0; }
.logout-btn{ background-color:#C62828; border:none; cursor:pointer; margin-top:auto; transition:background-color 0.3s; }
.logout-btn-mobile{ background-color:#C62828; border:none; cursor:pointer; transition:background-color 0.3s; display:none; }
@media(max-width:767px){ .logout-btn{ display:none; } .logout-btn-mobile{ display:block; } }
#mobile-header{ display:flex; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; min-height:100vh; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
.page-header { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; margin-bottom:18px; flex-wrap:wrap; }
.page-header h1 { margin:0; font-weight:400; }
.page-header p { margin:6px 0 0 0; color:#666; }
.card { background:#FFF; border:1px solid #EEE; border-radius:12px; box-shadow:0 2px 4px rgba(0,0,0,0.08); padding:18px; }
.card h2 { margin:0 0 14px 0; font-size:1.1em; font-weight:500; color:#1976D2; }
.flash, .error { padding:12px; border-radius:10px; margin-bottom:16px; }
.flash.success { background:#E8F5E9; border:1px solid #A5D6A7; color:#1B5E20; }
.flash.error, .error { background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; }
.subtabs { display:flex; gap:10px; margin:0 0 18px 0; border-bottom:1px solid #EEE; flex-wrap:wrap; }
.subtab-link { padding:10px 16px; border:1px solid transparent; border-bottom:none; border-radius:6px 6px 0 0; background:#F5F5F5; color:#555; text-decoration:none; }
.subtab-link.active { background:#1976D2; color:#FFF; border-color:#1976D2; }
.field-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }
.field { display:flex; flex-direction:column; gap:6px; margin-bottom:14px; }
.field label { color:#555; font-size:0.9em; }
.field input { border:1px solid #CCC; border-radius:6px; padding:10px; font:inherit; box-sizing:border-box; background:#FFF; }
.form-actions { display:flex; align-items:center; gap:12px; margin-top:8px; flex-wrap:wrap; }
.btn-primary { background:#1976D2; color:#FFF; border:none; border-radius:6px; padding:10px 14px; cursor:pointer; font:inherit; text-decoration:none; display:inline-flex; align-items:center; gap:8px; }
.btn-secondary { color:#1976D2; text-decoration:none; padding:10px 12px; display:inline-flex; align-items:center; justify-content:center; background:#FFF; border:1px solid #1976D2; border-radius:6px; cursor:pointer; font:inherit; }
.muted { color:#777; font-size:0.9em; }
.token-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:14px; flex-wrap:wrap; }
.token-list { display:grid; gap:12px; }
.token-item { border:1px solid #E5E7EB; border-radius:12px; padding:14px; background:#FAFBFD; }
.token-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:8px; flex-wrap:wrap; }
.token-name { font-weight:500; color:#202124; }
.token-meta { display:flex; flex-wrap:wrap; gap:12px; color:#666; font-size:0.9em; }
.token-create-form { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }
.token-modal-backdrop { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.55); z-index:2200; align-items:center; justify-content:center; padding:20px; box-sizing:border-box; }
.token-modal-backdrop.active { display:flex; }
.token-modal { width:min(520px, 100%); background:#FFF; border-radius:18px; box-shadow:0 24px 60px rgba(0,0,0,0.25); padding:22px; }
.token-modal h3 { margin:0 0 8px 0; font-weight:500; color:#1976D2; }
.token-modal p { margin:0 0 16px 0; color:#666; }
.token-display { display:flex; align-items:center; gap:10px; margin:16px 0; }
.token-display input { flex:1; border:1px solid #CCC; border-radius:8px; padding:12px; font:inherit; background:#F8FAFC; }
.token-actions { display:flex; justify-content:flex-end; gap:10px; flex-wrap:wrap; margin-top:18px; }
.general-row { display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
.general-row h2 { margin-bottom:6px; }
.general-row p { margin:0; color:#666; }
.session-table { border:1px solid #E0E0E0; border-radius:12px; background:#FFF; overflow:hidden; }
.session-head,.session-row { display:grid; grid-template-columns:minmax(120px,.8fr) minmax(120px,.85fr) minmax(130px,.8fr) minmax(260px,2fr) auto; gap:12px; align-items:start; padding:10px 14px; }
.session-head { background:#F8F9FA; color:#5F6368; font-size:0.78em; font-weight:600; letter-spacing:.03em; text-transform:uppercase; }
.session-row { border-top:1px solid #ECEFF1; }
.session-cell { min-width:0; color:#202124; line-height:1.32; overflow-wrap:anywhere; font-size:0.92em; }
.session-cell.muted { color:#5F6368; }
.session-primary { min-width:0; }
.session-title { font-weight:500; color:#202124; line-height:1.28; }
.session-subtitle { margin-top:2px; color:#5F6368; font-size:0.86em; line-height:1.34; white-space:pre-wrap; }
.session-actions { display:flex; gap:6px; justify-content:flex-end; flex-wrap:wrap; align-self:center; }
.session-badge { display:inline-flex; align-items:center; padding:4px 8px; border-radius:999px; background:#E3F2FD; color:#1565C0; font-size:0.78em; font-weight:500; }
.session-badge.current { background:#E8F5E9; color:#1B5E20; }
.session-footer { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-top:12px; flex-wrap:wrap; }
.session-limit-form { display:flex; align-items:center; gap:8px; flex-wrap:wrap; color:#666; }
.session-limit-form select { border:1px solid #CCC; border-radius:6px; padding:8px 10px; font:inherit; background:#FFF; }
.session-empty { padding:22px; text-align:center; color:#777; }
.session-history-card { margin-top:18px; }
@media(max-width:767px){ .session-head{display:none;} .session-row{grid-template-columns:1fr;gap:10px;} .session-cell[data-label]::before,.session-actions[data-label]::before{content:attr(data-label);display:block;font-size:0.76em;font-weight:600;letter-spacing:.03em;text-transform:uppercase;color:#777;margin-bottom:4px;} .session-actions{justify-content:flex-start;} }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.card{ border:1px solid #333; background-color:#1E1E1E; }
.card h2 { color:#BB86FC; }
.page-header p,.field label,.muted,.token-meta,.token-modal p,.general-row p{ color:#BBB; }
.field input,.token-display input { background:#121212; border-color:#444; color:#E0E0E0; }
.btn-primary { background:#BB86FC; color:#000; }
.btn-secondary { color:#BB86FC; border-color:#BB86FC; background:#1E1E1E; }
.flash.success { background:#12301A; border-color:#2E7D32; color:#C8E6C9; }
.flash.error,.error { background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
.subtabs { border-bottom-color:#333; }
.subtab-link { background:#2A2A2A; color:#BBB; }
.subtab-link.active { background:#BB86FC; color:#000; border-color:#BB86FC; }
.token-item { border-color:#333; background:#171A1F; }
.token-name { color:#EDEDED; }
.token-modal { background:#1E1E1E; }
.token-modal h3 { color:#BB86FC; }
.session-table { background:#1E1E1E; border-color:#333; }
.session-head { background:#202124; color:#BBB; }
.session-row { border-top-color:#333; background:#1E1E1E; }
.session-cell,.session-title { color:#EDEDED; }
.session-cell.muted,.session-subtitle,.session-limit-form,.session-empty { color:#BBB; }
.session-badge { background:#2A2433; color:#D9B8FF; }
.session-badge.current { background:#12301A; color:#C8E6C9; }
.session-limit-form select { background:#121212; border-color:#444; color:#E0E0E0; }
}
"""


def format_datetime(value):
    if not value or str(value) in {"0000-00-00 00:00:00", "None"}:
        return "Never"
    if hasattr(value, "strftime"):
        return f"{value.strftime('%b')} {value.day}, {value.year} {value.strftime('%I:%M %p').lstrip('0')}"
    return str(value)


def valid_datetime_local_string(value):
    if value == "":
        return True
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M")
        return True
    except ValueError:
        return False


def hash_password_value(password):
    salt = secrets.token_hex(16)
    return hashlib.sha256((password + salt).encode()).hexdigest(), salt


def fetch_api_tokens(user_id):
    ensure_api_token_schema()
    return query_all(
        """
        SELECT id, token_label, expires_at, created_at, last_used_at
        FROM api_tokens
        WHERE user_id=%s
        ORDER BY created_at DESC, id DESC
        """,
        (user_id,),
    )


def session_history_limit_value(raw_value):
    value = str(raw_value or "").strip().lower()
    if value == "all":
        return "all"
    try:
        number = int(value or "50")
    except ValueError:
        return 50
    return number if number in {50, 100, 250, 500} else 50


def session_limit_option_html(current, value, label):
    selected = str(current) == str(value)
    return f'<option value="{h(value)}"{" selected" if selected else ""}>{h(label)}</option>'


def handle_request():
    user = require_user()
    if not isinstance(user, dict):
        return user
    ctx = legacy_user_context(user)
    auth_provider = str((user or {}).get("auth_provider") or "local").strip().lower()
    synced_user = auth_provider in {"ldap", "oidc", "saml"}
    synced_password_change_url = identity_password_change_url(user, ctx["settings"])
    forced_password_change = user_requires_password_change(user)
    api_enabled = str(ctx["settings"].get("api_http_enable", "0")) == "1" and not forced_password_change
    requested_tab = str(request.args.get("tab") or "").strip()
    history_limit = session_history_limit_value(request.args.get("limit"))
    open_password_modal = str(request.args.get("open") or "").strip().lower() == "password" or forced_password_change
    active_tab = requested_tab if requested_tab in {"general", "api-keys", "sessions"} else "general"
    if active_tab == "api-keys" and not api_enabled:
        active_tab = "general"
    flash = ""
    error_html = ""
    new_api_token = session.pop("user_settings_new_api_token", {})

    if request.method == "POST":
        action = str(request.form.get("action") or "").strip()
        active_tab = str(request.form.get("tab") or active_tab).strip()
        if active_tab not in {"general", "api-keys", "sessions"}:
            active_tab = "general"
        if active_tab == "api-keys" and not api_enabled:
            active_tab = "general"
        if active_tab == "sessions":
            history_limit = session_history_limit_value(request.form.get("limit") or history_limit)
        if demo_mode_enabled() and action in {"change_password", "create_api_token"}:
            return demo_mode_iframe_html("user-settings")
        if action == "change_password":
            if synced_user:
                error_html = '<div class="error">To change your password, go through your Single Sign-On provider. Contact your system administrator for more information.</div>'
                active_tab = "general"
                open_password_modal = False
            else:
                current_password = str(request.form.get("current_password") or "")
                new_password = str(request.form.get("new_password") or "")
                confirm_password = str(request.form.get("confirm_password") or "")
                row = query_one("SELECT password, salt FROM users WHERE id=%s LIMIT 1", (user.get("id"),)) or {}
                current_hash = hashlib.sha256((current_password + str(row.get("salt") or "")).encode()).hexdigest()
                if not current_password or not new_password:
                    error_html = '<div class="error">Current password and new password are required.</div>'
                    open_password_modal = True
                elif current_hash != str(row.get("password") or ""):
                    error_html = '<div class="error">Current password is incorrect.</div>'
                    open_password_modal = True
                elif new_password != confirm_password:
                    error_html = '<div class="error">Password confirmation does not match.</div>'
                    open_password_modal = True
                else:
                    password_hash, salt = hash_password_value(new_password)
                    execute("UPDATE users SET password=%s, salt=%s, require_password_change=0 WHERE id=%s", (password_hash, salt, user.get("id")))
                    flash = '<div class="flash success">Password updated.</div>'
                    active_tab = "general"
        elif action == "create_api_token" and api_enabled:
            active_tab = "api-keys"
            token_label = str(request.form.get("api_token_label") or "").strip()[:API_TOKEN_LABEL_LENGTH]
            expires_at = str(request.form.get("api_token_expires_at") or "").strip()
            if not valid_datetime_local_string(expires_at):
                error_html = '<div class="error">API key expiration must use the local date and time picker format.</div>'
            else:
                try:
                    token_value = create_api_token_value()
                    expires_value = None
                    if expires_at:
                        expires_value = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M:%S")
                    execute(
                        "INSERT INTO api_tokens (user_id, token_hash, token_label, expires_at) VALUES (%s,%s,%s,%s)",
                        (user.get("id"), hash_api_token_value(token_value), token_label or None, expires_value),
                    )
                    session["user_settings_new_api_token"] = {"value": token_value, "label": token_label}
                    return redirect("/user/settings?tab=api-keys")
                except RuntimeError as exc:
                    error_html = f'<div class="error">{h(str(exc))}</div>'
        elif action in {"revoke_session", "logout_current_session"}:
            active_tab = "sessions"
            target_session_id = str(request.form.get("session_id") or "").strip()
            current_session_id = str(session.get("web_session_id") or "").strip()
            if not target_session_id:
                error_html = '<div class="error">Session not found.</div>'
            elif not active_user_session_record(target_session_id, user.get("id")):
                error_html = '<div class="error">Session not found.</div>'
            else:
                revoke_user_session_record(target_session_id, user.get("id"))
                if action == "logout_current_session" or target_session_id == current_session_id:
                    session.clear()
                    return redirect("/index")
                flash = '<div class="flash success">Session ended.</div>'

    tabs = ['<a class="subtab-link' + (" active" if active_tab == "general" else "") + '" href="/user/settings?tab=general">General</a>']
    tabs.append('<a class="subtab-link' + (" active" if active_tab == "sessions" else "") + '" href="/user/settings?tab=sessions">Sessions</a>')
    if api_enabled:
        tabs.append('<a class="subtab-link' + (" active" if active_tab == "api-keys" else "") + '" href="/user/settings?tab=api-keys">API Keys</a>')
    tabs_html = '<div class="subtabs">' + "".join(tabs) + "</div>"
    demo_form_attr = ' onsubmit="openDemoModePopup(\'user-settings\'); return false;"' if demo_mode_enabled() else ""

    if synced_user:
        password_body = (
            f'<a class="btn-primary" href="{h(synced_password_change_url)}" target="_blank" rel="noopener noreferrer"><i class="fa-solid fa-arrow-up-right-from-square"></i> Change Password</a>'
            if synced_password_change_url
            else '<p class="muted">To change your password, go through your Single Sign-On provider. Contact your system administrator for more information.</p>'
        )
        general_panel = f"""
    <div class="card">
        <div class="general-row">
            <div>
                <h2>Password</h2>
            </div>
            {password_body}
        </div>
    </div>"""
        open_password_modal = False
    else:
        general_panel = f"""
    <div class="card">
        <div class="general-row">
            <div>
                <h2>Password</h2>
            </div>
            <button class="btn-primary" type="button" onclick="openPasswordModal()"><i class="fa-solid fa-key"></i> Change Password</button>
        </div>
    </div>
    <div id="passwordModal" class="token-modal-backdrop{' active' if open_password_modal else ''}">
        <div class="token-modal">
            <h3>Change Password</h3>
            <form method="POST" action="/user/settings"{demo_form_attr}>
                <input type="hidden" name="action" value="change_password">
                <input type="hidden" name="tab" value="general">
                <div class="field">
                    <label for="current_password">Enter current password</label>
                    <input id="current_password" name="current_password" type="password" required>
                </div>
                <div class="field">
                    <label for="new_password">Enter new password</label>
                    <input id="new_password" name="new_password" type="password" required>
                </div>
                <div class="field">
                    <label for="confirm_password">Confirm new password</label>
                    <input id="confirm_password" name="confirm_password" type="password" required>
                </div>
                <div class="token-actions">
                    <button class="btn-secondary" type="button" onclick="closePasswordModal()">Close</button>
                    <button class="btn-primary" type="submit">Update Password</button>
                </div>
            </form>
        </div>
    </div>"""

    api_panel = ""
    if api_enabled:
        token_rows = fetch_api_tokens(user.get("id"))
        token_items = "".join(
            f"""<div class="token-item">
                <div class="token-head">
                    <div>
                        <div class="token-name">{h(row.get("token_label") or "Untitled key")}</div>
                    </div>
                </div>
                <div class="token-meta">
                    <span>Created: {h(format_datetime(row.get("created_at")))}</span>
                    <span>Last used: {h(format_datetime(row.get("last_used_at")))}</span>
                    <span>Expires: {h(format_datetime(row.get("expires_at")))}</span>
                </div>
            </div>"""
            for row in token_rows
        ) or '<div class="muted">No API keys yet.</div>'
        create_modal = f"""
    <div id="apiKeyCreateModal" class="token-modal-backdrop">
        <div class="token-modal">
            <h3>Create API Key</h3>
            <form method="POST" action="/user/settings"{demo_form_attr}>
                <input type="hidden" name="action" value="create_api_token">
                <input type="hidden" name="tab" value="api-keys">
                <div class="token-create-form">
                    <div class="field">
                        <label for="api_token_label">Label</label>
                        <input id="api_token_label" name="api_token_label" type="text" maxlength="{API_TOKEN_LABEL_LENGTH}" placeholder="Optional">
                    </div>
                    <div class="field">
                        <label for="api_token_expires_at">Expiration</label>
                        <input id="api_token_expires_at" name="api_token_expires_at" type="datetime-local">
                    </div>
                </div>
                <div class="token-actions">
                    <button class="btn-secondary" type="button" onclick="closeApiKeyCreateModal()">Close</button>
                    <button class="btn-primary" type="submit"><i class="fa-solid fa-key"></i> Create</button>
                </div>
            </form>
        </div>
    </div>"""
        reveal_modal = ""
        if isinstance(new_api_token, dict) and new_api_token.get("value"):
            reveal_modal = f"""
    <div id="apiKeyRevealModal" class="token-modal-backdrop active">
        <div class="token-modal">
            <h3>API Key Created</h3>
            <p>You will not be able to retrieve this key again.</p>
            <div class="token-display">
                <input id="new-api-key-value" type="password" value="{h(new_api_token.get("value"))}" readonly>
                <button class="btn-secondary" type="button" onclick="toggleNewApiKeyVisibility()">View</button>
            </div>
            <div class="token-actions">
                <button class="btn-primary" type="button" onclick="copyNewApiKey()">Copy</button>
                <button class="btn-secondary" type="button" onclick="closeApiKeyRevealModal()">Close</button>
            </div>
        </div>
    </div>"""
        api_panel = f"""
    <div class="card">
        <h2>API Keys</h2>
        <div class="token-toolbar">
            <button class="btn-primary" type="button" onclick="openApiKeyCreateModal()"><i class="fa-solid fa-plus"></i> Create</button>
        </div>
        <div class="token-list">{token_items}</div>
    </div>
    {create_modal}
    {reveal_modal}"""

    active_sessions = fetch_active_user_sessions(user.get("id"))
    current_session_id = str(session.get("web_session_id") or "").strip()
    history_rows = fetch_login_history_rows(user.get("id"), history_limit)
    history_total = login_history_total(user.get("id"))
    demo_sessions_banner = ""
    if demo_mode_enabled():
        now = datetime.now()
        current_rows = [row for row in active_sessions if str(row.get("session_id") or "").strip() == current_session_id]
        mock_active = [
            {
                "session_id": "demo-session-chrome-windows",
                "session_type": "web",
                "auth_provider": "local",
                "ip": "203.0.113.42",
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "created_at": now - timedelta(hours=3, minutes=12),
            },
            {
                "session_id": "demo-session-safari-iphone",
                "session_type": "web",
                "auth_provider": "local",
                "ip": "198.51.100.17",
                "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                "created_at": now - timedelta(days=1, hours=2),
            },
            {
                "session_id": "demo-session-desktop-client",
                "session_type": "desktop",
                "auth_provider": "local",
                "ip": "192.0.2.88",
                "user_agent": "OpenPagingServer Desktop Client/1.0 (Windows NT 10.0; Win64; x64)",
                "created_at": now - timedelta(days=2, hours=6),
            },
        ]
        active_sessions = current_rows + mock_active
        history_rows = [
            {
                "auth_provider": "local",
                "session_type": "web",
                "login_time": now - timedelta(hours=3, minutes=12),
                "ip": "203.0.113.42",
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "session_id": "demo-session-chrome-windows",
            },
            {
                "auth_provider": "local",
                "session_type": "web",
                "login_time": now - timedelta(days=1, hours=2),
                "ip": "198.51.100.17",
                "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                "session_id": "demo-session-safari-iphone",
            },
            {
                "auth_provider": "local",
                "session_type": "desktop",
                "login_time": now - timedelta(days=2, hours=6),
                "ip": "192.0.2.88",
                "user_agent": "OpenPagingServer Desktop Client/1.0 (Windows NT 10.0; Win64; x64)",
                "session_id": "demo-session-desktop-client",
            },
            {
                "auth_provider": "local",
                "session_type": "web",
                "login_time": now - timedelta(days=4, hours=9),
                "ip": "203.0.113.105",
                "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
                "session_id": "demo-session-firefox-linux",
            },
            {
                "auth_provider": "local",
                "session_type": "web",
                "login_time": now - timedelta(days=6, hours=1),
                "ip": "198.51.100.203",
                "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
                "session_id": "demo-session-edge-mac",
            },
        ]
        history_total = len(history_rows)
        demo_sessions_banner = '<div style="background:#1976D2;color:#FFF;padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:0.95em;"><i class="fa-solid fa-circle-info" style="margin-right:8px;"></i>Fictional content is shown here because demo mode is active</div>'
    history_shown = len(history_rows)
    active_session_rows = ""
    if active_sessions:
        rendered_rows = []
        for row in active_sessions:
            row_session_id = str(row.get("session_id") or "").strip()
            is_current = row_session_id == current_session_id
            action_label = "Logout" if is_current else "End Session"
            action_name = "logout_current_session" if is_current else "revoke_session"
            badge = '<span class="session-badge current">Current Session</span>' if is_current else '<span class="session-badge">Active</span>'
            rendered_rows.append(
                f"""<div class="session-row">
                    <div class="session-primary">
                        <div class="session-title">{badge}</div>
                        <div class="session-subtitle">{h(str(row.get("auth_provider") or "local").upper())} / {h(str(row.get("session_type") or "web").capitalize())}</div>
                    </div>
                    <div class="session-cell" data-label="Logged In">{h(format_datetime(row.get("created_at")))}</div>
                    <div class="session-cell" data-label="IP Address">{h(row.get("ip") or "Unknown")}</div>
                    <div class="session-cell" data-label="Client">{h(session_agent_label(row))}</div>
                    <div class="session-actions" data-label="Actions">
                        <form method="POST" action="/user/settings">
                            <input type="hidden" name="action" value="{action_name}">
                            <input type="hidden" name="tab" value="sessions">
                            <input type="hidden" name="limit" value="{h(history_limit)}">
                            <input type="hidden" name="session_id" value="{h(row_session_id)}">
                            <button class="btn-secondary" type="submit">{action_label}</button>
                        </form>
                    </div>
                </div>"""
            )
        active_session_rows = f"""<div class="session-table">
            <div class="session-head">
                <div>Status</div>
                <div>Logged In</div>
                <div>IP Address</div>
                <div>Client</div>
                <div>Actions</div>
            </div>
            {''.join(rendered_rows)}
        </div>"""
    else:
        active_session_rows = '<div class="session-empty">No active sessions found.</div>'

    history_render_rows = ""
    if history_rows:
        rendered_rows = []
        for row in history_rows:
            rendered_rows.append(
                f"""<div class="session-row">
                    <div class="session-primary">
                        <div class="session-title">{h(str(row.get("auth_provider") or "local").upper())}</div>
                        <div class="session-subtitle">{h(str(row.get("session_type") or "web").capitalize())}</div>
                    </div>
                    <div class="session-cell" data-label="Logged In">{h(format_datetime(row.get("login_time")))}</div>
                    <div class="session-cell" data-label="IP Address">{h(row.get("ip") or "Unknown")}</div>
                    <div class="session-cell" data-label="Client">{h(session_agent_label(row))}</div>
                    <div class="session-cell muted" data-label="Session">{h(str(row.get("session_id") or "")[:18] + ('...' if len(str(row.get('session_id') or '')) > 18 else '')) or 'N/A'}</div>
                </div>"""
            )
        history_render_rows = f"""<div class="session-table">
            <div class="session-head">
                <div>Provider</div>
                <div>Logged In</div>
                <div>IP Address</div>
                <div>Client</div>
                <div>Session</div>
            </div>
            {''.join(rendered_rows)}
        </div>"""
    else:
        history_render_rows = '<div class="session-empty">No login history yet.</div>'

    limit_options = "".join(
        session_limit_option_html(history_limit, value, label)
        for value, label in (("50", "50"), ("100", "100"), ("250", "250"), ("500", "500"), ("all", "All"))
    )
    sessions_panel = f"""
    {demo_sessions_banner}
    <div class="card">
        <h2>Current Sessions</h2>
        {active_session_rows}
    </div>
    <div class="card session-history-card">
        <h2>Login History</h2>
        {history_render_rows}
        <div class="session-footer">
            <div class="muted">Showing {h(history_shown)} of {h(history_total)}</div>
            <form method="GET" action="/user/settings" class="session-limit-form">
                <input type="hidden" name="tab" value="sessions">
                <label for="session_history_limit">Showing</label>
                <select id="session_history_limit" name="limit" onchange="this.form.submit()">
                    {limit_options}
                </select>
                <span>of {h(history_total)}</span>
            </form>
        </div>
    </div>"""

    script = """
<script>
function openPasswordModal() {
  const modal = document.getElementById('passwordModal');
  if (modal) modal.classList.add('active');
}
function closePasswordModal() {
  const modal = document.getElementById('passwordModal');
  if (modal) modal.classList.remove('active');
}
function openApiKeyCreateModal() {
  const modal = document.getElementById('apiKeyCreateModal');
  if (modal) modal.classList.add('active');
}
function closeApiKeyCreateModal() {
  const modal = document.getElementById('apiKeyCreateModal');
  if (modal) modal.classList.remove('active');
}
function closeApiKeyRevealModal() {
  const modal = document.getElementById('apiKeyRevealModal');
  if (modal) modal.classList.remove('active');
}
function toggleNewApiKeyVisibility() {
  const input = document.getElementById('new-api-key-value');
  if (!input) return;
  input.type = input.type === 'password' ? 'text' : 'password';
}
async function copyNewApiKey() {
  const input = document.getElementById('new-api-key-value');
  if (!input) return;
  try {
    await navigator.clipboard.writeText(input.value);
  } catch (_error) {
    input.type = 'text';
    input.select();
    document.execCommand('copy');
    input.type = 'password';
  }
}
document.addEventListener('click', function(event) {
  if (event.target && event.target.classList && event.target.classList.contains('token-modal-backdrop')) {
    event.target.classList.remove('active');
  }
});
</script>"""

    content = f"""<div class="page-header">
    <div>
        <h1>User Settings</h1>
    </div>
</div>
{flash}{error_html}
{tabs_html}
{general_panel if active_tab == "general" else ""}
{api_panel if active_tab == "api-keys" and api_enabled else ""}
{sessions_panel if active_tab == "sessions" else ""}
{script}"""
    return legacy_page("User Settings", ctx, "user-settings", USER_SETTINGS_STYLE, content)
