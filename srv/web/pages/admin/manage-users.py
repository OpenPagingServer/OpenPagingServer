
from srv.web.app import *

USERS_STYLE = r"""
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
#content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; }
.header-actions { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:18px; }
.header-actions h1 { margin:0; }
.card { background:#FFF; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); padding:16px; }
.card h2 { margin:0 0 14px 0; font-size:1.1em; font-weight:500; color:#1976D2; }
.summary-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,220px)); gap:12px; margin-bottom:18px; }
.summary-item { border:1px solid #EEE; border-radius:8px; padding:12px; background:#FFF; box-shadow:0 2px 4px rgba(0,0,0,0.08); }
.summary-item strong { display:block; font-size:1.4em; font-weight:500; }
.field-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }
.field { display:flex; flex-direction:column; gap:6px; margin-bottom:14px; }
.field label { color:#555; font-size:0.9em; }
.field input, .field select { border:1px solid #CCC; border-radius:4px; padding:10px; font:inherit; box-sizing:border-box; background:#FFF; }
.hint { color:#777; font-size:0.88em; margin-top:-8px; margin-bottom:12px; }
.btn-primary { background:#1976D2; color:#FFF; border:none; border-radius:4px; padding:10px 14px; cursor:pointer; font:inherit; text-decoration:none; display:inline-flex; align-items:center; gap:8px; }
.btn-secondary { color:#1976D2; text-decoration:none; padding:10px 12px; display:inline-flex; align-items:center; justify-content:center; background:#FFF; border:1px solid #1976D2; border-radius:4px; cursor:pointer; font:inherit; }
.user-list { list-style:none; margin:0; padding:0; }
.user-item { display:flex; justify-content:space-between; gap:14px; padding:14px 0; border-bottom:1px solid #EEE; }
.user-item:last-child { border-bottom:none; }
.user-main { flex:1; min-width:0; }
.user-name-row { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.user-name { font-weight:500; color:#202124; overflow-wrap:anywhere; }
.user-meta { color:#666; font-size:0.9em; margin-top:4px; overflow-wrap:anywhere; }
.user-stats { color:#777; font-size:0.88em; margin-top:6px; display:flex; flex-wrap:wrap; gap:10px; }
.group-actions { display:flex; align-items:center; gap:4px; }
.icon-action { width:36px; height:36px; border-radius:50%; color:#555; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; border:none; background:transparent; cursor:pointer; }
.icon-action:hover { background:rgba(25,118,210,0.08); color:#1976D2; }
.icon-action.delete:hover { background:rgba(198,40,40,0.08); color:#C62828; }
.role-badge { display:inline-flex; align-items:center; padding:4px 8px; border-radius:999px; background:#E3F2FD; color:#1565C0; font-size:0.8em; font-weight:500; }
.admin-badge { background:#FFF3E0; color:#E65100; }
.flash, .error { padding:12px; border-radius:8px; margin-bottom:16px; }
.flash.success { background:#E8F5E9; border:1px solid #A5D6A7; color:#1B5E20; }
.flash.error, .error { background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; }
.muted { color:#777; font-size:0.9em; }
.editor-card { margin-top:18px; }
.form-actions { display:flex; align-items:center; gap:12px; margin-top:8px; flex-wrap:wrap; }
.inline-note { font-size:0.9em; color:#666; }
.subtabs { display:flex; gap:10px; margin:0 0 18px 0; border-bottom:1px solid #EEE; flex-wrap:wrap; }
.subtab-link { padding:10px 16px; border:1px solid transparent; border-bottom:none; border-radius:6px 6px 0 0; background:#F5F5F5; color:#555; text-decoration:none; }
.subtab-link.active { background:#1976D2; color:#FFF; border-color:#1976D2; }
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
@media(max-width:767px){ .header-actions{ align-items:flex-start; flex-direction:column; } .user-item{ align-items:flex-start; flex-direction:column; } .group-actions{ margin-top:4px; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.card,.summary-item{ border:1px solid #333; background-color:#1E1E1E; }
.card h2 { color:#BB86FC; }
.field label,.muted,.hint,.inline-note,.user-meta,.user-stats{ color:#BBB; }
.field input,.field select { background:#121212; border-color:#444; color:#E0E0E0; }
.btn-primary { background:#BB86FC; color:#000; }
.btn-secondary { color:#BB86FC; border-color:#BB86FC; background:#1E1E1E; }
.user-item { border-bottom:1px solid #333; }
.user-name { color:#EDEDED; }
.icon-action { color:#BBB; }
.icon-action:hover { background:rgba(187,134,252,0.1); color:#BB86FC; }
.icon-action.delete:hover { background:rgba(244,67,54,0.12); color:#EF9A9A; }
.role-badge { background:#2D2340; color:#D8C2FF; }
.admin-badge { background:#3A2B1B; color:#FFCC80; }
.flash.success { background:#12301A; border-color:#2E7D32; color:#C8E6C9; }
.flash.error,.error { background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
.subtabs { border-bottom-color:#333; }
.subtab-link { background:#2A2A2A; color:#BBB; }
.subtab-link.active { background:#BB86FC; color:#000; border-color:#BB86FC; }
.token-item { border-color:#333; background:#171A1F; }
.token-name { color:#EDEDED; }
.token-meta,.token-modal p { color:#BBB; }
.token-modal { background:#1E1E1E; }
.token-modal h3 { color:#BB86FC; }
.token-display input { background:#121212; border-color:#444; color:#E0E0E0; }
}
"""

ROLE_OPTIONS = {
    "admin": "Administrator",
    "tempadmin": "Temporary Administrator",
    "user": "User",
    "tempuser": "Temporary User",
    "receiver": "Receiver",
    "tempreceiver": "Temporary Receiver",
}


def is_admin_role(role):
    return role in {"admin", "tempadmin"}


def role_label(role):
    return ROLE_OPTIONS.get(role, str(role or "").capitalize())


def format_date(value):
    if not value or str(value) in {"0000-00-00", "None"}:
        return "Never"
    if hasattr(value, "strftime"):
        return f"{value.strftime('%b')} {value.day}, {value.year}"
    return str(value)


def format_datetime(value):
    if not value or str(value) in {"0000-00-00 00:00:00", "None"}:
        return "Never"
    if hasattr(value, "strftime"):
        return f"{value.strftime('%b')} {value.day}, {value.year} {value.strftime('%I:%M %p').lstrip('0')}"
    return str(value)


def valid_date_string(value):
    if value == "":
        return True
    return re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or "") is not None


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


def admin_count():
    row = query_one("SELECT COUNT(*) AS c FROM users WHERE role IN ('admin', 'tempadmin')")
    return int(row.get("c") or 0)


def fetch_users():
    return query_all(
        """
        SELECT
            u.id, u.username, u.email, u.role, u.loginsleft, u.accountexpire, u.accountcreated,
            COALESCE(ls.logincount, 0) AS logincount, ls.lastlogin
        FROM users u
        LEFT JOIN (
            SELECT u2.id AS user_id, COUNT(la.id) AS logincount, MAX(la.attempt_time) AS lastlogin
            FROM users u2
            LEFT JOIN login_attempts la
                ON la.success = 1
               AND (la.username = u2.username OR (u2.email IS NOT NULL AND u2.email <> '' AND la.username = u2.email))
            GROUP BY u2.id
        ) ls ON ls.user_id = u.id
        ORDER BY u.username ASC
        """
    )


def fetch_user(user_id):
    for item in fetch_users():
        if str(item.get("id")) == str(user_id):
            return item
    return None


def flash_message(message, category):
    session["manage_users_flash"] = {"message": message, "type": category}


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


def format_datetime_local_value(value):
    if not value or str(value) in {"0000-00-00 00:00:00", "None"}:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%dT%H:%M")
    try:
        return datetime.strptime(str(value).split(".", 1)[0], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return ""


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    ensure_api_token_schema()
    ctx = legacy_user_context(user)
    api_enabled = str(ctx["settings"].get("api_http_enable", "0")) == "1"
    form_error = ""
    edit_user = None
    show_editor = False
    demo = demo_mode_enabled()
    editor_tab = "account"

    if request.method == "POST":
        action = request.form.get("action", "")
        requested_tab = request.form.get("editor_tab", "").strip()
        if requested_tab in {"account", "api-keys"}:
            editor_tab = requested_tab
        if demo and action in {"delete", "save", "create_api_token"}:
            return demo_mode_iframe_html("manage-users")
        if action == "delete":
            user_id = request.form.get("user_id", "")
            target = query_one("SELECT id, username, role FROM users WHERE id=%s LIMIT 1", (user_id,))
            if not target:
                flash_message("User not found.", "error")
            elif int(target.get("id") or 0) == 0:
                flash_message("User ID 0 cannot be deleted.", "error")
            elif str(target.get("id")) == str(user.get("id")):
                flash_message("You cannot delete the account you are currently signed in with.", "error")
            elif is_admin_role(target.get("role")) and admin_count() <= 1:
                flash_message("At least one administrator must remain on the server.", "error")
            else:
                execute("DELETE FROM users WHERE id=%s", (user_id,))
                flash_message("User deleted.", "success")
            return redirect("/admin/manage-users")

        if action == "create_api_token":
            editor_tab = "api-keys"
            user_id = request.form.get("user_id", "").strip()
            token_label = str(request.form.get("api_token_label") or "").strip()[:API_TOKEN_LABEL_LENGTH]
            expires_at = request.form.get("api_token_expires_at", "").strip()
            if not user_id:
                form_error = "User not found."
            elif not valid_datetime_local_string(expires_at):
                form_error = "API token expiration must use the local date and time picker format."
            else:
                target = fetch_user(user_id)
                if not target:
                    form_error = "User not found."
                else:
                    try:
                        token_value = create_api_token_value()
                        expires_value = None
                        if expires_at:
                            expires_value = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M:%S")
                        execute(
                            "INSERT INTO api_tokens (user_id, token_hash, token_label, expires_at) VALUES (%s,%s,%s,%s)",
                            (user_id, hash_api_token_value(token_value), token_label or None, expires_value),
                        )
                        session["manage_users_new_api_token"] = {"value": token_value, "label": token_label}
                        flash_message("API key created.", "success")
                        return redirect(f"/admin/manage-users?edit={user_id}&tab=api-keys")
                    except RuntimeError as exc:
                        form_error = str(exc)

        if action == "save":
            editor_tab = "account"
            user_id_raw = request.form.get("user_id", "").strip()
            user_id = user_id_raw or None
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip()
            role = request.form.get("role", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            expire = request.form.get("accountexpire", "").strip()
            logins_left = max(0, int(request.form.get("loginsleft") or 0))
            edit_user = {"id": user_id or "", "username": username, "email": email, "role": role, "accountexpire": expire, "loginsleft": logins_left}
            show_editor = True
            existing = fetch_user(user_id) if user_id else None
            if not username:
                form_error = "Username is required."
            elif role not in ROLE_OPTIONS:
                form_error = "Please choose a valid role."
            elif email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                form_error = "Email must be blank or a valid address."
            elif not valid_date_string(expire):
                form_error = "Account expiration must use the YYYY-MM-DD format."
            elif user_id is None and not password:
                form_error = "Password is required when creating a user."
            elif password and password != confirm:
                form_error = "Password confirmation does not match."
            elif user_id and not existing:
                form_error = "User not found."
            elif user_id and str(user_id) == str(user.get("id")) and not is_admin_role(role):
                form_error = "You cannot remove admin access from the account you are currently using."
            elif user_id and existing and is_admin_role(existing.get("role")) and not is_admin_role(role) and admin_count() <= 1:
                form_error = "At least one administrator must remain on the server."
            if not form_error:
                email_value = email or None
                expire_value = expire or None
                try:
                    if user_id is None:
                        password_hash, salt = hash_password_value(password)
                        execute(
                            "INSERT INTO users (username, email, password, salt, role, loginsleft, accountexpire) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (username, email_value, password_hash, salt, role, logins_left, expire_value),
                        )
                        flash_message("User created.", "success")
                    else:
                        if password:
                            password_hash, salt = hash_password_value(password)
                            execute(
                                "UPDATE users SET username=%s, email=%s, role=%s, loginsleft=%s, accountexpire=%s, password=%s, salt=%s WHERE id=%s",
                                (username, email_value, role, logins_left, expire_value, password_hash, salt, user_id),
                            )
                        else:
                            execute(
                                "UPDATE users SET username=%s, email=%s, role=%s, loginsleft=%s, accountexpire=%s WHERE id=%s",
                                (username, email_value, role, logins_left, expire_value, user_id),
                            )
                        flash_message("User updated.", "success")
                    return redirect("/admin/manage-users")
                except Exception:
                    form_error = "That username or email address is already in use."

    users = fetch_users()
    admin_users = sum(1 for row in users if is_admin_role(row.get("role")))
    if not show_editor:
        if request.args.get("edit"):
            edit_user = fetch_user(request.args.get("edit"))
            show_editor = bool(edit_user)
        elif "new" in request.args:
            edit_user = {"id": "", "username": "", "email": "", "role": "user", "loginsleft": 0, "accountexpire": "", "accountcreated": datetime.now()}
            show_editor = True
    query_tab = request.args.get("tab", "").strip()
    if query_tab in {"account", "api-keys"}:
        editor_tab = query_tab

    flash = session.pop("manage_users_flash", None)
    new_api_token = session.pop("manage_users_new_api_token", {})
    flash_html = f'<div class="flash {h(flash.get("type"))}">{h(flash.get("message"))}</div>' if isinstance(flash, dict) else ""
    error_html = f'<div class="error">{h(form_error)}</div>' if form_error else ""
    if show_editor:
        if demo:
            return demo_mode_iframe_html("manage-users")
        role_options = "".join(
            f'<option value="{h(value)}"{" selected" if (edit_user or {}).get("role") == value else ""}>{h(label)}</option>'
            for value, label in ROLE_OPTIONS.items()
        )
        password_required = " required" if not (edit_user or {}).get("id") else ""
        note = ""
        if (edit_user or {}).get("id"):
            note = f"""<div class="hint">Leave the password fields blank to keep the current password.</div>
                <div class="inline-note">
                    Created: {h(format_date((edit_user or {}).get("accountcreated")))} |
                    Last login: {h(format_datetime((edit_user or {}).get("lastlogin")))} |
                    Login count: {h((edit_user or {}).get("logincount") or 0)}
                </div>"""
        if not (edit_user or {}).get("id"):
            editor_tab = "account"
        tabs_html = ""
        if (edit_user or {}).get("id") and api_enabled:
            edit_id = h((edit_user or {}).get("id") or "")
            account_class = "subtab-link active" if editor_tab == "account" else "subtab-link"
            api_class = "subtab-link active" if editor_tab == "api-keys" else "subtab-link"
            tabs_html = f"""<div class="subtabs">
                <a class="{account_class}" href="/admin/manage-users?edit={edit_id}&tab=account">Account</a>
                <a class="{api_class}" href="/admin/manage-users?edit={edit_id}&tab=api-keys">API Keys</a>
            </div>"""
        api_token_panel = ""
        if (edit_user or {}).get("id") and api_enabled:
            token_rows = fetch_api_tokens((edit_user or {}).get("id"))
            create_modal = f"""
    <div id="apiKeyCreateModal" class="token-modal-backdrop">
        <div class="token-modal">
            <h3>Create API Key</h3>
            <p>Create a one-time key for this user. You can add an optional label and expiration date.</p>
            <form method="POST" action="/admin/manage-users">
                <input type="hidden" name="action" value="create_api_token">
                <input type="hidden" name="user_id" value="{h((edit_user or {}).get("id") or "")}">
                <input type="hidden" name="editor_tab" value="api-keys">
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
            api_token_panel = f"""
    <div class="card editor-card">
        <h2>API Keys</h2>
        <div class="token-toolbar">
            <button class="btn-primary" type="button" onclick="openApiKeyCreateModal()"><i class="fa-solid fa-plus"></i> Create</button>
        </div>
        <div class="token-list">{token_items}</div>
    </div>
    {create_modal}
    {reveal_modal}"""
        account_panel = f"""<form class="card editor-card" method="POST" action="/admin/manage-users">
        <h2>{"Edit User" if (edit_user or {}).get("id") else "New User"}</h2>
        <input type="hidden" name="action" value="save">
        <input type="hidden" name="user_id" value="{h((edit_user or {}).get("id") or "")}">
        <input type="hidden" name="editor_tab" value="account">
        <div class="field-grid">
            <div class="field"><label for="username">Username</label><input id="username" name="username" value="{h((edit_user or {}).get("username") or "")}" required></div>
            <div class="field"><label for="email">Email</label><input id="email" name="email" type="email" value="{h((edit_user or {}).get("email") or "")}" placeholder="Optional"></div>
            <div class="field"><label for="role">Role</label><select id="role" name="role" required>{role_options}</select></div>
            <div class="field"><label for="loginsleft">Uses Left</label><input id="loginsleft" name="loginsleft" type="number" min="0" value="{h((edit_user or {}).get("loginsleft") or 0)}"></div>
            <div class="field"><label for="accountexpire">Account Expires</label><input id="accountexpire" name="accountexpire" type="date" value="{h((edit_user or {}).get("accountexpire") or "")}"></div>
        </div>
        <div class="field-grid">
            <div class="field"><label for="password">{"New Password" if (edit_user or {}).get("id") else "Password"}</label><input id="password" name="password" type="password"{password_required}></div>
            <div class="field"><label for="confirm_password">{"Confirm New Password" if (edit_user or {}).get("id") else "Confirm Password"}</label><input id="confirm_password" name="confirm_password" type="password"{password_required}></div>
        </div>
        {note}
        <div class="form-actions">
            <button class="btn-primary" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save User</button>
            <a class="btn-secondary" href="/admin/manage-users">Cancel</a>
        </div>
    </form>"""
        modal_script = """
    <script>
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
        content = f"""    <div class="header-actions">
        <h1>{"Edit User" if (edit_user or {}).get("id") else "New User"}</h1>
        <a class="btn-secondary" href="/admin/manage-users"><i class="fa-solid fa-arrow-left"></i> Back</a>
    </div>
    {flash_html}{error_html}
    {tabs_html}
    {account_panel if editor_tab == "account" else ""}
    {api_token_panel if editor_tab == "api-keys" and api_enabled else ""}"""
        content += modal_script
    else:
        user_items = []
        for row in users:
            role = row.get("role") or ""
            badge = "role-badge admin-badge" if is_admin_role(role) else "role-badge"
            email = row.get("email") or "No email address"
            can_delete = int(row.get("id") or 0) != 0 and str(row.get("id")) != str(user.get("id")) and not (is_admin_role(role) and admin_users <= 1)
            delete_form = ""
            delete_onsubmit = "openDemoModePopup('manage-users'); return false;" if demo else "return confirm('Delete this user?')"
            if can_delete:
                delete_form = f"""<form method="POST" action="/admin/manage-users" onsubmit="{delete_onsubmit}">
                                        <input type="hidden" name="action" value="delete">
                                        <input type="hidden" name="user_id" value="{h(row.get("id"))}">
                                        <button class="icon-action delete" type="submit" title="Delete"><i class="fa-solid fa-trash"></i></button>
                                    </form>"""
            edit_href = "javascript:openDemoModePopup('manage-users')" if demo else f"/admin/manage-users?edit={h(row.get('id'))}"
            api_keys_link = ""
            if api_enabled:
                api_href = "javascript:openDemoModePopup('manage-users')" if demo else f"/admin/manage-users?edit={h(row.get('id'))}&tab=api-keys"
                api_keys_link = f'<a class="icon-action" href="{api_href}" title="API Keys"><i class="fa-solid fa-key"></i></a>'
            user_items.append(
                f"""<li class="user-item">
                            <div class="user-main">
                                <div class="user-name-row">
                                    <div class="user-name">{h(row.get("username"))}</div>
                                    <span class="{badge}">{h(role_label(role))}</span>
                                </div>
                                <div class="user-meta">{h(email)}</div>
                                <div class="user-stats">
                                    <span>Created: {h(format_date(row.get("accountcreated")))}</span>
                                    <span>Last login: {h(format_datetime(row.get("lastlogin")))}</span>
                                    <span>Uses left: {h(row.get("loginsleft") or 0)}</span>
                                    <span>Login count: {h(row.get("logincount") or 0)}</span>
                                    <span>Expires: {h(format_date(row.get("accountexpire")))}</span>
                                </div>
                            </div>
                            <div class="group-actions">
                                <a class="icon-action" href="{edit_href}" title="Edit"><i class="fa-solid fa-pen-to-square"></i></a>
                                {api_keys_link}
                                {delete_form}
                            </div>
                        </li>"""
            )
        new_href = "javascript:openDemoModePopup('manage-users')" if demo else "/admin/manage-users?new=1"
        content = f"""    <div class="header-actions">
        <h1>Manage Users</h1>
        <a class="btn-primary" href="{new_href}"><i class="fa-solid fa-plus"></i> New User</a>
    </div>
    {flash_html}{error_html}
    <div class="summary-grid">
        <div class="summary-item"><strong>{h(len(users))}</strong><span class="muted">Users</span></div>
        <div class="summary-item"><strong>{h(admin_users)}</strong><span class="muted">Administrators</span></div>
    </div>
    <div class="card">
        <h2>Users</h2>
        {'<ul class="user-list">' + ''.join(user_items) + '</ul>' if user_items else '<p class="muted">No users found.</p>'}
    </div>"""
    return legacy_page("Manage Users", ctx, "users", USERS_STYLE, content)
