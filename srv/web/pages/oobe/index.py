import xml.etree.ElementTree as ET

from srv.web.app import *

OOBE_STAGES = {"welcome", "account", "time", "modules", "analytics", "complete"}

OOBE_STYLE = r"""
body,html{margin:0;padding:0;min-height:100%;font-family:Tahoma,sans-serif;background:#e3f2fd;color:#202124}
.page{min-height:100vh;box-sizing:border-box;padding:28px;display:grid;grid-template-rows:auto 1fr;gap:28px}
.logo{width:280px;height:72px;display:flex;align-items:center}.logo img{max-width:100%;max-height:100%;object-fit:contain}.logo-dark{display:none}
.wrap{display:flex;align-items:center;justify-content:center}.card{background:#fff;border-radius:6px;box-shadow:0 4px 6px rgba(0,0,0,.1),0 1px 3px rgba(0,0,0,.08);padding:30px;max-width:560px;width:100%;box-sizing:border-box}
h1{color:#1976d2;font-weight:500;margin:0 0 12px}.lead{line-height:1.5;color:#424242}.field{position:relative;margin:22px 0}.field input,.field select{width:100%;box-sizing:border-box;padding:9px 0;border:0;border-bottom:2px solid #ccc;background:transparent;font-size:16px;outline:none;color:#333}.field input:focus,.field select:focus{border-bottom-color:#1976d2}.field label{display:block;color:#666;font-size:14px;margin-bottom:6px}.actions{display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap;margin-top:24px}.button{border:0;border-radius:4px;padding:12px 18px;background:#1976d2;color:#fff;font:inherit;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.button.secondary{background:#757575}.button.good{background:#2e7d32}.button.warn{background:#ef6c00}.error{background:#ffebee;border:1px solid #ef9a9a;color:#b71c1c;padding:10px;border-radius:6px;margin-bottom:14px}.notice{background:#e8f5e9;border:1px solid #a5d6a7;color:#1b5e20;padding:10px;border-radius:6px;margin-bottom:14px}.timebox{text-align:center;margin:24px 0}.time{font-size:40px;color:#1976d2}.date{font-size:18px;color:#555;margin-top:6px}.module-list{margin:18px 0 0;padding:0;list-style:none}.module-list li{padding:10px 0;border-bottom:1px solid #eee}.check{display:flex;gap:8px;align-items:center}.os-message{background:#fff3e0;border:1px solid #ffe0b2;color:#e65100;padding:10px;border-radius:6px}
@media(max-width:768px){.page{padding:18px;gap:12px}.logo{width:82%;height:auto;justify-self:center}.wrap{align-items:start}.card{border-radius:0;box-shadow:none;padding:22px;margin:0 -18px}.actions{justify-content:stretch}.button{flex:1}.time{font-size:34px}}
@media(prefers-color-scheme:dark){body,html{background:#121212;color:#e0e0e0}.card{background:#1e1e1e;box-shadow:0 4px 6px rgba(0,0,0,.6)}h1{color:#90caf9}.lead,.date{color:#bbb}.field input,.field select{color:#fff;border-bottom-color:#555}.field label{color:#ccc}.button{background:#90caf9;color:#121212}.button.secondary{background:#b0bec5}.button.good{background:#81c784}.button.warn{background:#ffb74d}.module-list li{border-bottom-color:#333}.notice{background:#14351a;border-color:#2e7d32;color:#c8e6c9}.error{background:#3b1515;border-color:#6d2a2a;color:#ffcdd2}.os-message{background:#3e2723;border-color:#5d4037;color:#ffb74d}.logo-light.dark-pair{display:none}.logo-dark{display:block}}
"""


def oobe_trigger_enabled():
    trigger = BASE_DIR / ".oobe"
    return trigger.is_file() and trigger.stat().st_size == 0


def oobe_user_count():
    try:
        row = query_one("SELECT COUNT(*) AS total FROM users")
        return int((row or {}).get("total") or 0)
    except Exception:
        return 1


def oobe_settings():
    defaults = {
        "product_name": "Open Paging Server",
        "favicon": "",
        "separate_dark_logo": "1",
        "enable_login_logo": "1",
        "login_logo_light": "/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png",
        "login_logo_dark": "/assets/OPENPAGINGSERVER-768x576-DARKMODE.png",
    }
    try:
        for row in query_all("SELECT parameter, value FROM systemsettings WHERE parameter IN ('product_name','favicon','separate_dark_logo','enable_login_logo','login_logo_light','login_logo_dark')"):
            defaults[str(row.get("parameter"))] = str(row.get("value") or "")
    except Exception:
        pass
    return defaults


def save_oobe_setting(parameter, value, description):
    execute("INSERT INTO systemsettings (`parameter`, `value`, `description`) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE `value`=VALUES(`value`), `description`=VALUES(`description`)", (parameter, value, description))


def oobe_stage():
    stage = request.form.get("stage") or request.args.get("stage") or "welcome"
    return stage if stage in OOBE_STAGES else "welcome"


def module_list():
    modules = []
    if ENDPOINT_MODULES_DIR.exists():
        for module_dir in ENDPOINT_MODULES_DIR.iterdir():
            if not module_dir.is_dir() or not re.fullmatch(r"[A-Za-z0-9_-]+", module_dir.name):
                continue
            info_path = module_dir / "info.xml"
            if not info_path.is_file():
                continue
            try:
                root = ET.parse(info_path).getroot()
            except ET.ParseError:
                continue
            name = (root.findtext("name") or module_dir.name).strip() or module_dir.name
            version = (root.findtext("version") or "").strip()
            author = (root.findtext("author") or "").strip()
            modules.append({"name": name, "version": version, "author": author})
    return sorted(modules, key=lambda item: item["name"].lower())


def logo_html(settings):
    if settings.get("enable_login_logo", "1") != "1":
        return ""
    separate = settings.get("separate_dark_logo", "0") == "1"
    if separate:
        return f"""<div class="logo"><img src="{h(settings.get("login_logo_light"))}" alt="{h(settings.get("product_name"))} logo" class="logo-light dark-pair"><img src="{h(settings.get("login_logo_dark"))}" alt="{h(settings.get("product_name"))} logo" class="logo-dark"></div>"""
    return f"""<div class="logo"><img src="{h(settings.get("login_logo_light"))}" alt="{h(settings.get("product_name"))} logo"></div>"""


def form_buttons(stage, back_stage=None, next_label="Next"):
    back = f'<button class="button secondary" name="action" value="back" type="submit">Back</button><input type="hidden" name="back_stage" value="{h(back_stage)}">' if back_stage else ""
    return f'<input type="hidden" name="stage" value="{h(stage)}"><div class="actions">{back}<button class="button" type="submit">{h(next_label)}</button></div>'


def stage_body(stage, settings, error, notice):
    now = datetime.now().astimezone()
    modules = module_list()
    alerts = (f'<div class="error">{h(error)}</div>' if error else "") + (f'<div class="notice">{h(notice)}</div>' if notice else "")
    if stage == "welcome":
    content = '<h1>Welcome to Open Paging Server</h1><p class="lead">You are a few steps away from getting started with your new paging system.</p><form method="post"><input type="hidden" name="stage" value="welcome"><div class="actions"><button class="button good" type="submit">Start</button></div></form>'
    elif stage == "account":
        content = """<h1>Create an account</h1><p class="lead">To begin, please create your user account. This will be the main administrator account, and cannot be deleted.</p>
        <form method="post"><input type="hidden" name="stage" value="account">
        <div class="field"><label>Username</label><input name="username" required autocomplete="username"></div>
        <div class="field"><label>Email (optional)</label><input type="email" name="email" autocomplete="email"></div>
        <div class="field"><label>Password</label><input type="password" name="password" required autocomplete="new-password"></div>
        <div class="field"><label>Confirm Password</label><input type="password" name="confirm_password" required autocomplete="new-password"></div>
        <div class="actions"><button class="button secondary" name="action" value="back" type="submit">Back</button><input type="hidden" name="back_stage" value="welcome"><button class="button" type="submit">Next</button></div></form>"""
    elif stage == "time":
        content = f"""<h1>Is this date and time correct?</h1><p class="lead">If not, ensure this system is using the correct NTP server and timezone. Correct date &amp; time is important for bells, scheduled broadcasts, history, message expiration, and general housekeeping.</p>
        <div class="timebox"><div class="time" id="serverTime" data-iso="{h(now.isoformat())}">{h(now.strftime("%I:%M %p").lstrip("0"))}</div><div class="date" id="serverDate" data-iso="{h(now.isoformat())}">{h(now.strftime("%A %b %d, %Y"))}</div></div>
        <form method="post"><input type="hidden" name="stage" value="time"><div class="actions"><button class="button secondary" name="action" value="back" type="submit">Back</button><input type="hidden" name="back_stage" value="account"><button class="button good" name="action" value="next" type="submit">Next</button></div></form>"""
    elif stage == "modules":
        items = "".join(f"<li>{h(item['name'])}{(' ' + h(item['version'])) if item['version'] else ''}{(' by ' + h(item['author'])) if item['author'] else ''}</li>" for item in modules) or "<li>No endpoint modules found.</li>"
        content = f"""<h1>Endpoint modules</h1><p class="lead">Open Paging Server uses endpoint modules. You have the following endpoint modules installed:</p><ul class="module-list">{items}</ul><p class="lead">You can add more in /opt/openpagingserver/endpoint-modules</p>
        <form method="post"><input type="hidden" name="stage" value="modules"><div class="actions"><button class="button secondary" name="action" value="back" type="submit">Back</button><input type="hidden" name="back_stage" value="time"><button class="button" type="submit">Next</button></div></form>"""
    elif stage == "analytics":
        content = """<h1>Would you like to enable optional analytics?</h1><p class="lead">To help the Open Paging Server project improve, you can opt-in to share optional analytics. Analytics contain mainly anonymous data such as your operating system, software versions, anonymized crash logs, etc. And may include your public IP address. You can change this setting later.</p>
        <form method="post"><input type="hidden" name="stage" value="analytics"><div class="actions"><button class="button secondary" name="action" value="back" type="submit">Back</button><input type="hidden" name="back_stage" value="modules"><button class="button warn" name="action" value="continue_disabled" type="submit">Continue disabled</button><button class="button good" name="action" value="opt_in" type="submit">Opt-in</button></div></form>"""
    else:
        content = '<h1>Setup complete!</h1><p class="lead">To continue, login with your username and password you just made.</p><p class="lead">Happy Paging!</p><div class="actions"><a class="button" href="/">Login</a></div>'
    script = """<script>
const serverTime=document.getElementById('serverTime'),serverDate=document.getElementById('serverDate');
if(serverTime&&serverDate){const d=new Date(serverTime.dataset.iso);if(!Number.isNaN(d.getTime())){serverTime.textContent=new Intl.DateTimeFormat(undefined,{hour:'numeric',minute:'2-digit'}).format(d);serverDate.textContent=new Intl.DateTimeFormat(undefined,{weekday:'long',month:'short',day:'numeric',year:'numeric'}).format(d);}}
</script>"""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Setup - {h(settings["product_name"])}</title>{f'<link rel="icon" href="{h(settings["favicon"])}" type="image/x-icon">' if settings.get("favicon") else ""}<style>{OOBE_STYLE}</style></head><body><div class="page">{logo_html(settings)}<main class="wrap"><section class="card">{alerts}{content}</section></main></div>{script}</body></html>"""


def handle_request():
    if not oobe_trigger_enabled() or oobe_user_count() > 0:
        return redirect("/")
    settings = oobe_settings()
    stage = oobe_stage()
    error = ""
    notice = ""
    if request.method == "POST":
        action = request.form.get("action", "next")
        if action == "back":
            stage = request.form.get("back_stage", "welcome")
        elif stage == "welcome":
            stage = "account"
        elif stage == "account":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            if not username:
                error = "Username is required."
            elif email and "@" not in email:
                error = "Email must be blank or a valid address."
            elif not password:
                error = "Password is required."
            elif password != confirm:
                error = "Password confirmation does not match."
            else:
                salt = secrets.token_hex(16)
                session["oobe_user"] = {"username": username, "email": email, "password": hashlib.sha256((password + salt).encode()).hexdigest(), "salt": salt}
                stage = "time"
        elif stage == "time":
            stage = "modules"
        elif stage == "modules":
            stage = "analytics"
        elif stage == "analytics":
            analytics = "1" if action == "opt_in" else "0"
            save_oobe_setting("analytics", analytics, "Send optional analytics to the Open Paging Server project. Privacy Policy: https://www.openpagingserver.org/privacypolicy/analytics")
            pending_user = session.get("oobe_user")
            if not pending_user:
                stage = "welcome"
                error = "Please create the administrator account first."
            elif oobe_user_count() == 0:
                execute("SET SESSION sql_mode = IF(FIND_IN_SET('NO_AUTO_VALUE_ON_ZERO', @@sql_mode), @@sql_mode, CONCAT_WS(',', @@sql_mode, 'NO_AUTO_VALUE_ON_ZERO'))")
                execute("INSERT INTO users (id, username, email, password, salt, role, userperm, adminperm) VALUES (0,%s,%s,%s,%s,'admin','all','all')", (pending_user["username"], pending_user["email"] or None, pending_user["password"], pending_user["salt"]))
                session.pop("oobe_user", None)
                try:
                    (BASE_DIR / ".oobe").unlink(missing_ok=True)
                except OSError:
                    pass
                stage = "complete"
            else:
                return redirect("/")
    return Response(stage_body(stage, settings, error, notice), mimetype="text/html")
