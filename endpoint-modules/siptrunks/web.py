import html
import ipaddress
import re


TRUNK_TABLE = "sip-trunks"
DIALPLAN_TABLE = "endpoints-input-siptrunk"


def h(value):
    return html.escape("" if value is None else str(value), quote=True)


def forms():
    return {
        "ip": {
            "label": "IP SIP Trunk",
            "description": "Trust SIP requests from a specific trunk IP address.",
        },
        "auth": {
            "label": "Authenticated SIP Trunk",
            "description": "Authenticate SIP requests with a username and password.",
        },
        "dialplan": {
            "label": "SIP Dialplan Extension",
            "description": "Route a SIP extension to paging, messaging, test tone, or echo test.",
        },
    }


def query_all(conn_factory, sql, params=None):
    conn = conn_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()


def execute(conn_factory, sql, params=None):
    conn = conn_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.commit()
    finally:
        conn.close()


def table_columns(conn_factory, table):
    try:
        conn = conn_factory()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SHOW COLUMNS FROM `{table}`")
                return {row.get("Field") for row in cur.fetchall() if row.get("Field")}
        finally:
            conn.close()
    except Exception:
        return set()


def ensure_column(conn_factory, table, column, sql):
    columns = table_columns(conn_factory, table)
    if column not in columns:
        execute(conn_factory, f"ALTER TABLE `{table}` ADD {sql}")


def ensure_schema(conn_factory):
    conn = conn_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{TRUNK_TABLE}` ("
                "`id` INT NOT NULL AUTO_INCREMENT, "
                "`status` VARCHAR(255) NOT NULL DEFAULT 'Offline', "
                "`auth` VARCHAR(32) NOT NULL DEFAULT 'IP', "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`username` VARCHAR(255) DEFAULT NULL, "
                "`password` VARCHAR(255) DEFAULT NULL, "
                "`ipaddr` VARCHAR(255) NOT NULL DEFAULT '0.0.0.0', "
                "`holdbehavior` VARCHAR(32) NOT NULL DEFAULT 'passrtp', "
                "PRIMARY KEY (`id`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{DIALPLAN_TABLE}` ("
                "`id` INT NOT NULL AUTO_INCREMENT, "
                "`name` VARCHAR(255) NOT NULL DEFAULT '', "
                "`extension` VARCHAR(100) NOT NULL DEFAULT '', "
                "`group` VARCHAR(255) DEFAULT NULL, "
                "`trigger` VARCHAR(100) NOT NULL DEFAULT 'page', "
                "`passcode` VARCHAR(64) DEFAULT NULL, "
                "PRIMARY KEY (`id`), KEY `extension_idx` (`extension`)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
            )
        conn.commit()
    finally:
        conn.close()
    ensure_column(conn_factory, TRUNK_TABLE, "status", "`status` VARCHAR(255) NOT NULL DEFAULT 'Offline'")
    ensure_column(conn_factory, TRUNK_TABLE, "auth", "`auth` VARCHAR(32) NOT NULL DEFAULT 'IP'")
    ensure_column(conn_factory, TRUNK_TABLE, "name", "`name` VARCHAR(255) NOT NULL DEFAULT ''")
    ensure_column(conn_factory, TRUNK_TABLE, "username", "`username` VARCHAR(255) DEFAULT NULL")
    ensure_column(conn_factory, TRUNK_TABLE, "password", "`password` VARCHAR(255) DEFAULT NULL")
    ensure_column(conn_factory, TRUNK_TABLE, "ipaddr", "`ipaddr` VARCHAR(255) NOT NULL DEFAULT '0.0.0.0'")
    ensure_column(conn_factory, TRUNK_TABLE, "holdbehavior", "`holdbehavior` VARCHAR(32) NOT NULL DEFAULT 'passrtp'")
    ensure_column(conn_factory, DIALPLAN_TABLE, "name", "`name` VARCHAR(255) NOT NULL DEFAULT ''")
    ensure_column(conn_factory, DIALPLAN_TABLE, "extension", "`extension` VARCHAR(100) NOT NULL DEFAULT ''")
    ensure_column(conn_factory, DIALPLAN_TABLE, "group", "`group` VARCHAR(255) DEFAULT NULL")
    ensure_column(conn_factory, DIALPLAN_TABLE, "trigger", "`trigger` VARCHAR(100) NOT NULL DEFAULT 'page'")
    ensure_column(conn_factory, DIALPLAN_TABLE, "passcode", "`passcode` VARCHAR(64) DEFAULT NULL")


def valid_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def valid_ip_or_network(value):
    try:
        if "/" in str(value):
            ipaddress.ip_network(value, strict=False)
        else:
            ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def clean_groups(raw):
    if isinstance(raw, list):
        parts = raw
    else:
        parts = re.split(r"[.,\s]+", str(raw or ""))
    clean = []
    for part in (p.strip() for p in parts):
        if part and part not in clean:
            clean.append(part)
    return ".".join(clean)


def sip_hold_column(conn_factory):
    columns = table_columns(conn_factory, TRUNK_TABLE)
    for candidate in ("holdbehavior", "holdbehaviour", "hold-behavior", "holdbehabior", "hold-behavipr"):
        if candidate in columns:
            return candidate
    try:
        execute(conn_factory, f"ALTER TABLE `{TRUNK_TABLE}` ADD `holdbehavior` VARCHAR(32) NOT NULL DEFAULT 'passrtp'")
        return "holdbehavior"
    except Exception:
        return None


def fetch_groups(conn_factory):
    try:
        rows = query_all(conn_factory, "SELECT `id`, `name` FROM `groups` ORDER BY CAST(`id` AS UNSIGNED), `id`")
    except Exception:
        rows = []
    return [{"id": "0", "name": "All Recipients"}] + rows


def fetch_messages(conn_factory):
    try:
        columns = table_columns(conn_factory, "messages")
        id_column = "messageid" if "messageid" in columns else "id" if "id" in columns else None
        if not id_column:
            return []
        name_column = "name" if "name" in columns else id_column
        return query_all(conn_factory, f"SELECT `{id_column}` AS id, `{name_column}` AS name FROM `messages` ORDER BY CAST(`{id_column}` AS UNSIGNED), `{id_column}`")
    except Exception:
        return []


def dialplan_trigger(trigger_type, message_id):
    if trigger_type == "message":
        return "message:" + str(message_id or "").strip()
    if trigger_type in {"page", "#testtone", "#echotest"}:
        return trigger_type
    return "page"


def split_dialplan_trigger(value):
    value = str(value or "page").strip()
    if value.startswith("message:"):
        return "message", value.split(":", 1)[1]
    if value in {"page", "#testtone", "#echotest"}:
        return value, ""
    return "page", ""


def frame(body):
    return (
        "<style>body{font-family:Tahoma,sans-serif;margin:0;padding:20px;color:#202124;background:#fff}"
        ".form-surface{max-width:720px;background:#fff;border:1px solid #e6e8eb;border-radius:8px;padding:18px;box-shadow:0 1px 3px rgba(0,0,0,.08)}"
        ".grid{display:grid;gap:14px}.row{display:grid;gap:6px}label{font-weight:500}.control,input,select{padding:10px 11px;border:1px solid #ccd1d5;border-radius:6px;font:inherit;box-sizing:border-box;width:100%;background:#fff;color:#202124}.short-control{max-width:180px}"
        "button,.button{background:#1976D2;color:#fff;border:0;border-radius:6px;padding:10px 14px;font:inherit;cursor:pointer;justify-self:start;text-decoration:none}"
        ".success{background:#E8F5E9;border:1px solid #A5D6A7;color:#1B5E20;padding:10px;border-radius:6px;margin-bottom:12px}.error{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}"
        ".dropdown-checklist{position:relative}.dropdown-checklist summary{list-style:none;cursor:pointer;padding:10px 11px;border:1px solid #ccd1d5;border-radius:6px;background:#fff}.dropdown-checklist summary::-webkit-details-marker{display:none}.dropdown-panel{position:absolute;top:calc(100% + 6px);left:0;right:0;z-index:20;border:1px solid #d8dde2;border-radius:6px;padding:8px;display:grid;gap:6px;max-height:220px;overflow:auto;background:#fff;box-shadow:0 8px 18px rgba(0,0,0,.14)}"
        ".check{display:flex;gap:8px;align-items:center;font-weight:400}.check.disabled{opacity:.55}.switch-row{display:flex;align-items:center;gap:10px}.switch{position:relative;width:44px;height:24px}.switch input{opacity:0;width:0;height:0}.slider{position:absolute;cursor:pointer;inset:0;background:#9aa0a6;border-radius:999px;transition:.2s}.slider:before{content:\"\";position:absolute;height:18px;width:18px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s;box-shadow:0 1px 2px rgba(0,0,0,.25)}.switch input:checked + .slider{background:#1976D2}.switch input:checked + .slider:before{transform:translateX(20px)}.hint{color:#5f6368;font-size:.9em}"
        "@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.form-surface{background:#232323;border-color:#333;box-shadow:none}.control,input,select,.dropdown-checklist summary,.dropdown-panel{background:#171717;border-color:#3a3a3a;color:#eee}button,.button{background:#BB86FC;color:#000}.hint{color:#aaa}.switch input:checked + .slider{background:#BB86FC}}</style>"
        + body
    )


def render_dialplan_fields(values, groups, messages):
    selected_groups = set(values["group"].split(".")) if values["group"] else set()
    group_options = "".join(
        f"""<label class="check"><input type="checkbox" class="group-check" value="{h(row.get("id"))}" data-label="{h("All Recipients" if str(row.get("id")) == "0" else row.get("name") or row.get("id"))}"{" checked" if str(row.get("id")) in selected_groups else ""}> {h("All Recipients" if str(row.get("id")) == "0" else str(row.get("id")) + (" - " + str(row.get("name")) if row.get("name") else ""))}</label>"""
        for row in groups
    )
    if not group_options:
        group_options = '<span class="hint">No groups configured.</span>'
    message_options = "".join(
        f'<option value="{h(row.get("id"))}"{" selected" if str(row.get("id")) == values["message_id"] else ""}>{h(row.get("id"))} - {h(row.get("name") or "")}</option>'
        for row in messages
    )
    trigger_options = "".join(
        f'<option value="{h(value)}"{" selected" if value == values["trigger_type"] else ""}>{h(label)}</option>'
        for value, label in (("page", "Paging"), ("message", "Send Message"), ("#testtone", "Milliwatt Test Tone"), ("#echotest", "Echo Test"))
    )
    return f"""<div class="row"><label>Name</label><input class="control" name="name" value="{h(values["name"])}" required></div>
<div class="row"><label>Extension</label><input class="control short-control" name="extension" id="extension" value="{h(values["extension"])}" required pattern="[0-9*#]*" inputmode="tel"></div>
<div class="row"><label>Trigger</label><select class="control" name="trigger_type" id="triggerType">{trigger_options}</select></div>
<div class="row trigger-extra" id="messageRow"><label>Message</label><select class="control" name="message_id"><option value="">Choose a message</option>{message_options}</select></div>
<div class="row trigger-extra" id="groupRow"><label>Groups</label><input type="hidden" name="group" id="groupValue" value="{h(values["group"])}"><details class="dropdown-checklist" id="groupDropdown"><summary id="groupSummary">Select groups</summary><div class="dropdown-panel">{group_options}</div></details></div>
<label class="switch-row"><span>Use a passcode</span><span class="switch"><input type="checkbox" name="require_passcode" value="1" id="requirePasscode"{" checked" if values["require_passcode"] == "1" else ""}><span class="slider"></span></span></label>
<div class="row" id="passcodeRow"><label>Passcode</label><input class="control short-control" name="passcode" id="passcode" value="{h(values["passcode"])}" pattern="[0-9A-D]*" inputmode="text"></div>
<script>
const triggerType = document.getElementById('triggerType');
const groupRow = document.getElementById('groupRow');
const messageRow = document.getElementById('messageRow');
const requirePasscode = document.getElementById('requirePasscode');
const passcodeRow = document.getElementById('passcodeRow');
const passcode = document.getElementById('passcode');
const extension = document.getElementById('extension');
const groupValue = document.getElementById('groupValue');
const groupChecks = Array.from(document.querySelectorAll('.group-check'));
const groupSummary = document.getElementById('groupSummary');
function syncTrigger() {{
  const value = triggerType.value;
  groupRow.style.display = (value === 'page' || value === 'message') ? 'grid' : 'none';
  messageRow.style.display = value === 'message' ? 'grid' : 'none';
}}
function syncPasscode() {{
  passcodeRow.style.display = requirePasscode.checked ? 'grid' : 'none';
  if (!requirePasscode.checked) passcode.value = '';
}}
function syncGroupsFromChecks() {{
  const selectedInputs = groupChecks.filter(input => input.checked);
  const selected = selectedInputs.map(input => input.value);
  groupValue.value = selected.join('.');
  groupSummary.textContent = selectedInputs.length ? selectedInputs.map(input => input.dataset.label || input.value).join(', ') : 'Select groups';
}}
function syncAllRecipients() {{
  const all = groupChecks.find(input => input.value === '0');
  if (!all) {{
    syncGroupsFromChecks();
    return;
  }}
  if (all.checked) {{
    groupChecks.forEach(input => {{
      if (input !== all) {{
        input.checked = false;
        input.disabled = true;
        input.closest('.check')?.classList.add('disabled');
      }}
    }});
  }} else {{
    groupChecks.forEach(input => {{
      input.disabled = false;
      input.closest('.check')?.classList.remove('disabled');
    }});
  }}
  syncGroupsFromChecks();
}}
function blockInvalidInput(input, pattern) {{
  input.addEventListener('beforeinput', event => {{
    if (event.data && !pattern.test(event.data)) event.preventDefault();
  }});
}}
triggerType.addEventListener('change', syncTrigger);
requirePasscode.addEventListener('change', syncPasscode);
passcode.addEventListener('input', () => {{ passcode.value = passcode.value.toUpperCase().replace(/[^0-9A-D]/g, ''); }});
extension.addEventListener('input', () => {{ extension.value = extension.value.replace(/[^0-9*#]/g, ''); }});
blockInvalidInput(extension, /^[0-9*#]+$/);
blockInvalidInput(passcode, /^[0-9A-Da-d]+$/);
groupChecks.forEach(input => input.addEventListener('change', syncAllRecipients));
document.getElementById('dialplanForm').addEventListener('submit', syncGroupsFromChecks);
syncTrigger();
syncPasscode();
syncAllRecipients();
</script>"""


def render_form(form_type, request, conn_factory, page, user):
    ensure_schema(conn_factory)
    if form_type not in forms():
        return page("Endpoint Form", "<h1>Endpoint form not found</h1>", "endpoints", user, status=404)
    error = ""
    values = {
        "ip": {"name": "", "ipaddr": ""},
        "auth": {"name": "", "username": "", "password": "", "ipaddr": "0.0.0.0"},
        "dialplan": {"name": "", "extension": "", "group": "", "trigger_type": "page", "message_id": "", "require_passcode": "", "passcode": ""},
    }[form_type]
    if request.method == "POST":
        if form_type == "ip":
            values["name"] = request.form.get("name", "").strip()
            values["ipaddr"] = request.form.get("ipaddr", "").strip()
            if not values["name"] or not values["ipaddr"]:
                error = "Name and IP address are required."
            elif not valid_ip(values["ipaddr"]):
                error = "Enter a valid IP address."
            elif query_all(conn_factory, f"SELECT id FROM `{TRUNK_TABLE}` WHERE auth='IP' AND ipaddr=%s", (values["ipaddr"],)):
                error = "That SIP trunk IP already exists."
            else:
                execute(conn_factory, f"INSERT INTO `{TRUNK_TABLE}` (name, auth, username, password, ipaddr, status) VALUES (%s,'IP',NULL,NULL,%s,'Offline')", (values["name"], values["ipaddr"]))
                return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p class='success'>IP SIP trunk added.</p>", "endpoints", user)
        elif form_type == "auth":
            for key in values:
                values[key] = request.form.get(key, values[key]).strip()
            if not values["name"] or not values["username"] or not values["password"]:
                error = "Name, username, and password are required."
            elif not valid_ip_or_network(values["ipaddr"] or "0.0.0.0"):
                error = "Enter a valid IP restriction, such as 0.0.0.0 or 10.50.10.0/24."
            elif query_all(conn_factory, f"SELECT id FROM `{TRUNK_TABLE}` WHERE auth='USERPASS' AND username=%s", (values["username"],)):
                error = "That SIP trunk username already exists."
            else:
                if not values["ipaddr"]:
                    values["ipaddr"] = "0.0.0.0"
                execute(conn_factory, f"INSERT INTO `{TRUNK_TABLE}` (name, auth, username, password, ipaddr, status) VALUES (%s,'USERPASS',%s,%s,%s,'Offline')", (values["name"], values["username"], values["password"], values["ipaddr"]))
                return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p class='success'>Authenticated SIP trunk added.</p>", "endpoints", user)
        elif form_type == "dialplan":
            for key in values:
                values[key] = request.form.get(key, values[key]).strip()
            values["require_passcode"] = "1" if request.form.get("require_passcode") else ""
            values["group"] = clean_groups(values["group"])
            trigger = dialplan_trigger(values["trigger_type"], values["message_id"])
            passcode = values["passcode"].upper() if values["require_passcode"] == "1" else ""
            if values["trigger_type"] not in {"page", "message"}:
                values["group"] = ""
            if not values["name"] or not values["extension"]:
                error = "Name and extension are required."
            elif not re.fullmatch(r"[0-9*#]+", values["extension"]):
                error = "Extension can only contain 0-9, *, and #."
            elif values["trigger_type"] not in {"page", "message", "#testtone", "#echotest"}:
                error = "Choose a valid trigger."
            elif values["trigger_type"] == "message" and not values["message_id"]:
                error = "Choose a message."
            elif values["trigger_type"] in {"page", "message"} and not values["group"]:
                error = "Choose at least one group."
            elif passcode and not re.fullmatch(r"[0-9A-D]+", passcode):
                error = "Passcode can only contain 0-9 and A-D."
            elif query_all(conn_factory, f"SELECT id FROM `{DIALPLAN_TABLE}` WHERE extension=%s", (values["extension"],)):
                error = "That SIP extension already exists."
            else:
                execute(conn_factory, f"INSERT INTO `{DIALPLAN_TABLE}` (`name`, `extension`, `group`, `trigger`, `passcode`) VALUES (%s,%s,%s,%s,%s)", (values["name"], values["extension"], values["group"] or None, trigger, passcode or None))
                return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p class='success'>SIP dialplan extension added.</p>", "endpoints", user)
    if form_type == "ip":
        body = f"""<style>body{{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}}.grid{{display:grid;gap:12px}}.row{{display:grid;gap:6px}}label{{font-weight:500}}.control{{padding:10px;border:1px solid #ddd;border-radius:4px;font:inherit}}.button{{background:#1976D2;color:#fff;border:0;border-radius:4px;padding:10px 14px;font:inherit;cursor:pointer}}.success{{background:#E8F5E9;border:1px solid #A5D6A7;color:#1B5E20;padding:10px;border-radius:6px;margin-bottom:12px}}.error{{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}}@media(prefers-color-scheme:dark){{body{{background:#1e1e1e;color:#e0e0e0}}.control{{background:#171717;border-color:#333;color:#eee}}.button{{background:#BB86FC;color:#000}}}}</style>
{f'<div class="error">{h(error)}</div>' if error else ''}
<form method="post" class="grid">
    <div class="row"><label>Name</label><input class="control" name="name" value="{h(values["name"])}" required></div>
    <div class="row"><label>IP Address</label><input class="control" name="ipaddr" value="{h(values["ipaddr"])}" required></div>
    <button class="button" type="submit">Add IP SIP Trunk</button>
</form>"""
        return page(forms()[form_type]["label"], body, "endpoints", user)
    if form_type == "auth":
        body = f"""<style>body{{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}}.grid{{display:grid;gap:12px}}.row{{display:grid;gap:6px}}label{{font-weight:500}}.control{{padding:10px;border:1px solid #ddd;border-radius:4px;font:inherit}}.button{{background:#1976D2;color:#fff;border:0;border-radius:4px;padding:10px 14px;font:inherit;cursor:pointer}}.success{{background:#E8F5E9;border:1px solid #A5D6A7;color:#1B5E20;padding:10px;border-radius:6px;margin-bottom:12px}}.error{{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}}@media(prefers-color-scheme:dark){{body{{background:#1e1e1e;color:#e0e0e0}}.control{{background:#171717;border-color:#333;color:#eee}}.button{{background:#BB86FC;color:#000}}}}</style>
{f'<div class="error">{h(error)}</div>' if error else ''}
<form method="post" class="grid">
    <div class="row"><label>Name</label><input class="control" name="name" value="{h(values["name"])}" required></div>
    <div class="row"><label>Username</label><input class="control" name="username" value="{h(values["username"])}" required></div>
    <div class="row"><label>Password</label><input class="control" type="password" name="password" value="{h(values["password"])}" required></div>
    <div class="row"><label>IP Restriction</label><input class="control" name="ipaddr" value="{h(values["ipaddr"])}" required></div>
    <button class="button" type="submit">Add Authenticated SIP Trunk</button>
</form>"""
        return page(forms()[form_type]["label"], body, "endpoints", user)
    groups = fetch_groups(conn_factory)
    messages = fetch_messages(conn_factory)
    fields = render_dialplan_fields(values, groups, messages)
    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    body = f'{error_html}<form method="post" class="grid form-surface" id="dialplanForm">{fields}<button class="button" type="submit">Add SIP Dialplan Extension</button></form>'
    return page(forms()[form_type]["label"], frame(body), "endpoints", user)


def render_dialplan_action_form(row, error, groups, messages):
    trigger_type, message_id = split_dialplan_trigger(row.get("trigger"))
    values = {
        "name": str(row.get("name") or ""),
        "extension": str(row.get("extension") or ""),
        "group": str(row.get("group") or ""),
        "trigger_type": trigger_type,
        "message_id": message_id,
        "require_passcode": "1" if row.get("passcode") else "",
        "passcode": str(row.get("passcode") or ""),
    }
    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    return f'{error_html}<form method="post" class="grid form-surface" id="dialplanForm">{render_dialplan_fields(values, groups, messages)}<button class="button" type="submit">Save SIP Dialplan Extension</button></form>'


def render_trunk_action_form(row, error):
    auth_type = str(row.get("auth") or "IP").upper()
    hold_value = str(row.get("holdbehavior") or "passrtp").lower()
    options = "".join(
        f'<option value="{h(value)}"{" selected" if hold_value == value else ""}>{h(label)}</option>'
        for value, label in (("passrtp", "Pass RTP"), ("pausertp", "Pause RTP"), ("endcall", "End Call"))
    )
    auth_fields = (
        f"""<div class="row"><label>IP Address</label><input class="control" name="ipaddr" value="{h(row.get("ipaddr"))}" required></div>"""
        if auth_type == "IP"
        else f"""<div class="row"><label>Username</label><input class="control" name="username" value="{h(row.get("username"))}" required></div>
            <div class="row"><label>Password</label><input class="control" type="password" name="password" value="{h(row.get("password"))}" required></div>
            <div class="row"><label>IP Restriction</label><input class="control" name="ipaddr" value="{h(row.get("ipaddr") or "0.0.0.0")}" required></div>"""
    )
    return f"""<style>body{{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}}.grid{{display:grid;gap:12px}}.row{{display:grid;gap:6px}}label{{font-weight:500}}.control{{padding:10px;border:1px solid #ddd;border-radius:4px;font:inherit}}.button{{background:#1976D2;color:#fff;border:0;border-radius:4px;padding:10px 14px;font:inherit;cursor:pointer}}.success{{background:#E8F5E9;border:1px solid #A5D6A7;color:#1B5E20;padding:10px;border-radius:6px;margin-bottom:12px}}.error{{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}}.meta{{color:#5f6368;margin:0 0 14px}}@media(prefers-color-scheme:dark){{body{{background:#1e1e1e;color:#e0e0e0}}.control{{background:#171717;border-color:#333;color:#eee}}.button{{background:#BB86FC;color:#000}}.meta{{color:#aaa}}}}</style>
{f'<div class="error">{h(error)}</div>' if error else ''}
<p class="meta">Current status: {h(row.get("status") or "Offline")}</p>
<form method="post" class="grid">
    <div class="row"><label>Name</label><input class="control" name="name" value="{h(row.get("name"))}" required></div>
    {auth_fields}
    <div class="row"><label>Hold Behavior</label><select class="control" name="holdbehavior">{options}</select></div>
    <button class="button" type="submit">Save SIP Trunk</button>
</form>"""


def render_delete_action_form(row, kind, error):
    if kind == "dialplan":
        detail = f'SIP Trunk Extension {("(" + h(row.get("extension")) + ")") if row.get("extension") else ""}'
        button = "Delete SIP Dialplan Extension"
    else:
        detail = h("Authenticated SIP Trunk" if str(row.get("auth") or "").upper() == "USERPASS" else "SIP Trunk")
        if row.get("ipaddr"):
            detail += f" ({h(row.get('ipaddr'))})"
        button = "Delete SIP Trunk"
    return f"""<style>body{{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}}.grid{{display:grid;gap:12px}}.button{{background:#C62828;color:#fff;border:0;border-radius:4px;padding:10px 14px;font:inherit;cursor:pointer}}.error{{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}}.meta{{color:#5f6368;margin:0 0 14px}}@media(prefers-color-scheme:dark){{body{{background:#1e1e1e;color:#e0e0e0}}.meta{{color:#aaa}}}}</style>
{f'<div class="error">{h(error)}</div>' if error else ''}
<form method="post" class="grid">
    <p class="meta">Delete {h(row.get("name") or "")}?</p>
    <div>{detail}</div>
    <button class="button" type="submit">{h(button)}</button>
</form>"""


def parse_endpoint_id(endpoint_id):
    token = str(endpoint_id or "")
    if "-" not in token:
        return "", ""
    kind, row_id = token.split("-", 1)
    return kind, row_id


def render_action(action, endpoint_id, request, conn_factory, page, user):
    ensure_schema(conn_factory)
    kind, row_id = parse_endpoint_id(endpoint_id)
    if action not in {"edit", "delete"} or kind not in {"trunk", "dialplan"} or not row_id.isdigit():
        return page("Endpoint Action", "<h1>Invalid endpoint action</h1>", "endpoints", user, status=400)
    table = TRUNK_TABLE if kind == "trunk" else DIALPLAN_TABLE
    hold_column = sip_hold_column(conn_factory) if kind == "trunk" else None
    rows = query_all(conn_factory, f"SELECT * FROM `{table}` WHERE id=%s LIMIT 1", (row_id,))
    if not rows:
        return page("Endpoint Action", "<h1>Endpoint not found</h1>", "endpoints", user, status=404)
    row = rows[0]
    if kind == "trunk" and hold_column and hold_column != "holdbehavior":
        try:
            hold_rows = query_all(conn_factory, f"SELECT `{hold_column}` AS holdbehavior FROM `{table}` WHERE id=%s LIMIT 1", (row_id,))
            if hold_rows:
                row = dict(row)
                row["holdbehavior"] = hold_rows[0].get("holdbehavior") or "passrtp"
        except Exception:
            pass
    error = ""
    if request.method == "POST":
        if action == "delete":
            execute(conn_factory, f"DELETE FROM `{table}` WHERE id=%s", (row_id,))
            return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p class='success'>Endpoint saved.</p>", "endpoints", user)
        if kind == "trunk":
            name = request.form.get("name", "").strip()
            ipaddr = request.form.get("ipaddr", "").strip()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            holdbehavior = request.form.get("holdbehavior", "passrtp").strip().lower()
            auth_type = str(row.get("auth") or "IP").upper()
            if not name:
                error = "Name is required."
            elif holdbehavior not in {"passrtp", "pausertp", "endcall"}:
                holdbehavior = "passrtp"
            if not error and auth_type == "IP":
                if not ipaddr or not valid_ip(ipaddr):
                    error = "Enter a valid IP address."
                elif query_all(conn_factory, f"SELECT id FROM `{TRUNK_TABLE}` WHERE auth='IP' AND ipaddr=%s AND id<>%s", (ipaddr, row_id)):
                    error = "That SIP trunk IP already exists."
                else:
                    hold_sql = f", `{hold_column}`=%s" if hold_column else ""
                    params = [name, ipaddr]
                    if hold_column:
                        params.append(holdbehavior)
                    params.append(row_id)
                    execute(conn_factory, f"UPDATE `{table}` SET name=%s, username=NULL, password=NULL, ipaddr=%s{hold_sql} WHERE id=%s", tuple(params))
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p class='success'>Endpoint saved.</p>", "endpoints", user)
            elif not error:
                if not username or not password:
                    error = "Username and password are required."
                elif not ipaddr:
                    ipaddr = "0.0.0.0"
                if not error and not valid_ip_or_network(ipaddr):
                    error = "Enter a valid IP restriction."
                elif not error and query_all(conn_factory, f"SELECT id FROM `{TRUNK_TABLE}` WHERE auth='USERPASS' AND username=%s AND id<>%s", (username, row_id)):
                    error = "That SIP trunk username already exists."
                elif not error:
                    hold_sql = f", `{hold_column}`=%s" if hold_column else ""
                    params = [name, username, password, ipaddr]
                    if hold_column:
                        params.append(holdbehavior)
                    params.append(row_id)
                    execute(conn_factory, f"UPDATE `{table}` SET name=%s, username=%s, password=%s, ipaddr=%s{hold_sql} WHERE id=%s", tuple(params))
                    return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p class='success'>Endpoint saved.</p>", "endpoints", user)
            row = dict(row)
            row.update({"name": name, "ipaddr": ipaddr, "username": username, "password": password, "holdbehavior": holdbehavior})
        else:
            name = request.form.get("name", "").strip()
            extension = request.form.get("extension", "").strip()
            trigger_type = request.form.get("trigger_type", request.form.get("trigger", "page")).strip()
            message_id = request.form.get("message_id", "").strip()
            group = clean_groups(request.form.get("group", ""))
            passcode = request.form.get("passcode", "").strip().upper() if request.form.get("require_passcode") else ""
            trigger = dialplan_trigger(trigger_type, message_id)
            duplicate = query_all(conn_factory, f"SELECT id FROM `{DIALPLAN_TABLE}` WHERE extension=%s AND id<>%s", (extension, row_id))
            if trigger_type not in {"page", "message"}:
                group = ""
            if not name or not extension:
                error = "Enter a name and extension."
            elif not re.fullmatch(r"[0-9*#]+", extension):
                error = "Extension can only contain 0-9, *, and #."
            elif trigger_type not in {"page", "message", "#testtone", "#echotest"}:
                error = "Choose a valid trigger."
            elif trigger_type == "message" and not message_id:
                error = "Choose a message."
            elif trigger_type in {"page", "message"} and not group:
                error = "Choose at least one group."
            elif passcode and not re.fullmatch(r"[0-9A-D]+", passcode):
                error = "Passcode can only contain 0-9 and A-D."
            elif duplicate:
                error = "A dialplan entry already exists for that extension."
            else:
                execute(conn_factory, f"UPDATE `{table}` SET name=%s, extension=%s, `group`=%s, trigger=%s, passcode=%s WHERE id=%s", (name, extension, group or None, trigger, passcode or None, row_id))
                return page("Endpoint Saved", "<script>window.top.location.href='/admin/manage-endpoints'</script><p class='success'>Endpoint saved.</p>", "endpoints", user)
            row = dict(row)
            row.update({"name": name, "extension": extension, "group": group, "trigger": trigger, "passcode": passcode})
    if action == "delete":
        body = render_delete_action_form(row, kind, error)
    elif kind == "trunk":
        body = render_trunk_action_form(row, error)
    else:
        body = render_dialplan_action_form(row, error, fetch_groups(conn_factory), fetch_messages(conn_factory))
    return page("Endpoint Action", frame(body), "endpoints", user)


def render_settings(request, conn_factory, page, user):
    return page("SIP Trunk Settings", "<h1>SIP Trunk Settings</h1><p>No additional settings are required for this module.</p>", "endpoints", user)