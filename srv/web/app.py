import hashlib
import html
import importlib.util
import json
import os
import re
import secrets
import socket
import sys
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pymysql
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_file,
    send_from_directory,
    session,
)
from werkzeug.utils import secure_filename


WEB_DIR = Path(__file__).resolve().parent
BASE_DIR = WEB_DIR.parent.parent
WEB_ROOT_DIR = BASE_DIR / "srv" / "web"
WEB_PAGES_DIR = WEB_ROOT_DIR / "pages"
WEB_STATIC_DIR = WEB_ROOT_DIR / "assets"
WEB_ERROR_DIR = WEB_ROOT_DIR / "errors"
ENDPOINT_MODULES_DIR = BASE_DIR / "endpoint-modules"
ASSET_DIR = Path(os.getenv("ASSET_PATH", "/var/lib/openpagingserver/assets"))
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
APP_DEBUG = str(os.getenv("DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}
TRACEBACKS = {}

app = Flask(
    __name__,
    static_folder=str(WEB_STATIC_DIR),
    static_url_path="/bundled-assets",
)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or hashlib.sha256((DB_PASS or "openpagingserver").encode()).hexdigest()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=128 * 1024 * 1024,
)


def dispatch_web_page(relative_path):
    page_path = (WEB_PAGES_DIR / relative_path).resolve()
    if page_path.suffix == "":
        page_path = page_path.with_suffix(".py")
    root = WEB_PAGES_DIR.resolve()
    if root not in page_path.parents and page_path != root:
        abort(404)
    if not page_path.is_file():
        abort(404)
    module_name = "ops_web_page_" + re.sub(r"[^A-Za-z0-9_]", "_", relative_path)
    spec = importlib.util.spec_from_file_location(module_name, page_path)
    if spec is None or spec.loader is None:
        abort(404)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = getattr(module, "handle_request", None)
    if handler is None:
        abort(404)
    return handler()


def db(cursorclass=pymysql.cursors.DictCursor):
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=cursorclass,
        autocommit=False,
    )


def query_all(sql, params=None):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()


def query_one(sql, params=None):
    rows = query_all(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            lastrowid = cur.lastrowid
        conn.commit()
        return lastrowid
    finally:
        conn.close()


def execute_many(statements):
    conn = db()
    try:
        with conn.cursor() as cur:
            for sql, params in statements:
                cur.execute(sql, params or ())
        conn.commit()
    finally:
        conn.close()


def settings():
    rows = query_all("SELECT parameter, value FROM systemsettings")
    return {str(row["parameter"]): "" if row["value"] is None else str(row["value"]) for row in rows}


def save_setting(parameter, value, description):
    execute(
        """
        INSERT INTO systemsettings (`parameter`, `value`, `description`)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE `value` = VALUES(`value`), `description` = VALUES(`description`)
        """,
        (parameter, value, description),
    )


def table_columns(table_name):
    rows = query_all(f"SHOW COLUMNS FROM `{table_name}`")
    return {row["Field"] for row in rows if row.get("Field")}


def truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return query_one("SELECT id, username, role, adminperm, userperm FROM users WHERE id=%s LIMIT 1", (user_id,))


def require_user():
    user = current_user()
    if not user:
        return redirect("/")
    return user


def require_admin():
    user = require_user()
    if not isinstance(user, dict):
        return user
    if user.get("role") not in {"admin", "tempadmin"}:
        abort(403)
    return user


def require_non_receiver():
    user = require_user()
    if not isinstance(user, dict):
        return user
    if user.get("role") in {"receiver", "tempreceiver"}:
        return redirect("/dashboard")
    return user


def h(value):
    return html.escape("" if value is None else str(value), quote=True)


def product_context():
    data = settings()
    return {
        "settings": data,
        "product_name": data.get("product_name") or "Open Paging Server",
        "favicon": data.get("favicon") or "",
        "show_online_docs": data.get("show_online_docs", "1"),
    }


def ops_sidebar_brand_html(data, product_name):
    data = data if isinstance(data, dict) else {}
    product_name = "" if product_name is None else str(product_name)
    use_logo = truthy(data.get("use_logo_in_sidebar", "1"))
    light_logo = str(data.get("sidebar_logo_light") or "/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png").strip()
    dark_logo = str(data.get("sidebar_logo_dark") or "/assets/OPENPAGINGSERVER-768x576-DARKMODE.png").strip()
    if not use_logo or not light_logo:
        return f'<div class="sidebar-brand"><span>{h(product_name)}</span></div>'
    dark_source = f'<source media="(prefers-color-scheme: dark)" srcset="{h(dark_logo)}">' if dark_logo else ""
    return (
        '<div class="sidebar-brand sidebar-brand-logo"><picture>'
        f'{dark_source}<img src="{h(light_logo)}" alt="{h(product_name)}">'
        "</picture></div>"
    )


def legacy_user_context(user=None):
    if user is None:
        user = current_user()
    user = user or {}
    role = user.get("role") or ""
    if user.get("id") and not role:
        role_row = query_one("SELECT role FROM users WHERE id = %s LIMIT 1", (user.get("id"),)) or {}
        role = role_row.get("role") or ""
    data = settings()
    product_name = data.get("product_name") or "Open Paging Server"
    return {
        "user": user,
        "role": role,
        "is_admin": role in {"admin", "tempadmin"},
        "is_receiver": role in {"receiver", "tempreceiver"},
        "settings": data,
        "product_name": product_name,
        "favicon": data.get("favicon") or "",
        "show_online_docs": data.get("show_online_docs", "1"),
        "brand_html": ops_sidebar_brand_html(data, product_name),
    }


def legacy_sidebar_html(ctx, active):
    links = [
        ("/dashboard", "house", "Dashboard", "dashboard"),
        ("/paging/", "bullhorn", "Paging", "paging"),
        ("/messages/", "message", "Messages", "messages"),
        ("/history/", "clock-rotate-left", "History", "history"),
        ("/bells/", "bell", "Bells", "bells"),
        ("/assets/", "folder-open", "Assets", "assets"),
    ]
    rendered = [ctx["brand_html"]]
    for href, icon, label, key in links:
        cls = ' class="active"' if active == key else ""
        rendered.append(
            f'<a href="{h(href)}"{cls}><span class="nav-icon"><i class="fa-solid fa-{icon}"></i></span><span class="nav-label">{h(label)}</span></a>'
        )
    if ctx.get("is_admin"):
        admin_links = [
            ("/admin/manage-users", "users-cog", "Manage Users"),
            ("/admin/manage-endpoints", "shapes", "Manage Endpoints"),
            ("/admin/manage-groups", "user-group", "Manage Groups"),
            ("/admin/settings/general", "cogs", "Server Settings"),
        ]
        for href, icon, label in admin_links:
            rendered.append(
                f'<a href="{h(href)}" class="admin-only"><span class="nav-icon"><i class="fa-solid fa-{icon}"></i></span><span class="nav-label">{h(label)}</span></a>'
            )
    if ctx.get("show_online_docs") == "1":
        rendered.append(
            '<a href="https://docs.openpagingserver.org"><span class="nav-icon"><i class="fa-solid fa-book"></i></span><span class="nav-label">Online Documentation</span></a>'
        )
    rendered.append(
        '<button class="logout-btn" onclick="logout()"><span class="nav-icon"><i class="fa-solid fa-sign-out-alt"></i></span><span class="nav-label">Logout</span></button>'
    )
    return "\n    ".join(rendered)


def legacy_page(title, ctx, active, style, content, extra_script="", extra_after=""):
    favicon_html = f'<link rel="icon" href="{h(ctx.get("favicon"))}" type="image/x-icon">' if ctx.get("favicon") else ""
    common_sidebar_style = """
#sidebar a,.logout-btn,.admin-only{display:flex!important;align-items:center;gap:10px}
#sidebar .nav-icon,.logout-btn .nav-icon,.admin-only .nav-icon{width:20px;display:inline-flex;justify-content:center;flex:0 0 20px}
#sidebar .nav-label,.logout-btn .nav-label,.admin-only .nav-label{min-width:0}
#sidebar a i,.logout-btn i,.admin-only i{margin-right:0!important;width:auto!important;text-align:center}
.logout-btn{display:flex!important}
.logout-btn-mobile{display:none!important}
"""
    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{h(title)} - {h(ctx["product_name"])}</title>
{favicon_html}
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet" />
<link href="/assets/sidebar-brand.css" rel="stylesheet" />
<style>
{style}
{common_sidebar_style}
</style>
</head>
<body>
<div id="mobile-header">
    <span class="hamburger" onclick="toggleSidebar()"><i class="fa-solid fa-bars"></i></span>
    {ctx["brand_html"]}
</div>
<div id="overlay" onclick="closeSidebar()"></div>
<div id="sidebar">
    {legacy_sidebar_html(ctx, active)}
</div>
<div id="content" onclick="closeSidebarOnContentClick()">
{content}
</div>
<script>
function toggleSidebar() {{
  const sidebar = document.getElementById("sidebar");
  sidebar.classList.toggle("open");
  document.getElementById("overlay").classList.toggle("active", sidebar.classList.contains("open"));
}}
function closeSidebar() {{
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("overlay").classList.remove("active");
}}
function closeSidebarOnContentClick() {{
  if (document.getElementById("sidebar").classList.contains("open")) closeSidebar();
}}
function logout() {{
  window.location.href = "/logout";
}}
{extra_script}
</script>
{extra_after}
</body>
</html>"""
    return Response(html_doc, mimetype="text/html")


def sidebar(active="dashboard", is_admin=False, is_receiver=False):
    ctx = product_context()
    data = ctx["settings"]
    brand = h(ctx["product_name"])
    if truthy(data.get("use_logo_in_sidebar", "1")):
        logo = data.get("sidebar_logo_light") or "/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png"
        brand = f'<img class="sidebar-logo" src="{h(logo)}" alt="{h(ctx["product_name"])}">'
    links = [("/dashboard", "house", "Dashboard", "dashboard")]
    if not is_receiver:
        links.extend(
            [
                ("/paging/", "bullhorn", "Paging", "paging"),
                ("/messages/", "message", "Messages", "messages"),
                ("/history/", "clock-rotate-left", "History", "history"),
                ("/bells/", "bell", "Bells", "bells"),
                ("/assets/", "folder-open", "Assets", "assets"),
            ]
        )
        if is_admin:
            links.extend(
                [
                    ("/admin/manage-users", "users-cog", "Manage Users", "users"),
                    ("/admin/manage-endpoints", "shapes", "Manage Endpoints", "endpoints"),
                    ("/admin/manage-groups", "user-group", "Manage Groups", "groups"),
                    ("/admin/settings/general", "cogs", "Server Settings", "settings"),
                ]
            )
    if ctx["show_online_docs"] == "1":
        links.append(("https://docs.openpagingserver.org", "book", "Online Documentation", "docs"))
    rendered = [f'<div class="brand">{brand}</div>']
    for href, icon, label, key in links:
        cls = "active" if active == key else ""
        rendered.append(
            f'<a class="{cls}" href="{h(href)}"><span class="nav-icon"><i class="fa-solid fa-{icon}"></i></span><span class="nav-label">{h(label)}</span></a>'
        )
    rendered.append(
        '<a class="logout" href="/logout"><span class="nav-icon"><i class="fa-solid fa-sign-out-alt"></i></span><span class="nav-label">Logout</span></a>'
    )
    return "\n".join(rendered)


BASE_CSS = """
body,html{margin:0;padding:0;font-family:Tahoma,Arial,sans-serif;background:#fff;color:#222;min-height:100%}
a{color:#1976d2;text-decoration:none}a:hover{text-decoration:underline}
.layout{display:flex;min-height:100vh}.sidebar{width:220px;background:#1976d2;color:white;position:fixed;inset:0 auto 0 0;display:flex;flex-direction:column;box-shadow:2px 0 8px rgba(0,0,0,.2)}
.sidebar a,.sidebar .brand{color:white;display:flex;align-items:center;gap:10px;padding:12px 20px;border-bottom:1px solid rgba(255,255,255,.14);box-sizing:border-box}.sidebar a.active,.sidebar a:hover{background:#1565c0;text-decoration:none}.sidebar .nav-icon{width:24px;display:inline-flex;justify-content:center;flex:0 0 24px}.sidebar .logout{margin-top:auto;background:#c62828}.sidebar-logo{display:block;max-width:170px;max-height:64px;object-fit:contain;margin:auto}
.content{margin-left:220px;padding:24px;box-sizing:border-box;width:calc(100% - 220px)}h1,h2{font-weight:400;color:#1976d2}.card{border:1px solid #e5e5e5;border-radius:8px;padding:16px;margin:0 0 16px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}.actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input,select,textarea{box-sizing:border-box;width:100%;padding:10px;border:1px solid #bbb;border-radius:4px;font:inherit;background:#fff;color:#222}textarea{min-height:110px}.button,button{display:inline-flex;align-items:center;gap:8px;border:0;border-radius:4px;background:#1976d2;color:#fff;padding:10px 14px;font:inherit;cursor:pointer;text-decoration:none}.button:hover,button:hover{background:#1565c0;text-decoration:none}.danger{background:#c62828}.danger:hover{background:#b71c1c}.muted{color:#666}.table{width:100%;border-collapse:collapse}.table th,.table td{border-bottom:1px solid #eee;text-align:left;padding:10px;vertical-align:top}.flash{padding:12px;border-radius:4px;margin-bottom:16px}.success{background:#e8f5e9;color:#1b5e20}.error{background:#ffebee;color:#b71c1c}.tabs{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}.tabs a{padding:10px 14px;background:#f5f5f5;border-radius:5px 5px 0 0}.tabs a.active{background:#1976d2;color:#fff;text-decoration:none}.pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#e3f2fd;color:#1565c0}
@media(max-width:767px){.layout{display:block}.sidebar{position:static;width:100%;min-height:auto}.content{margin-left:0;width:100%;padding:16px}.table{display:block;overflow-x:auto}}
@media(prefers-color-scheme:dark){body,html{background:#121212;color:#e0e0e0}.sidebar{background:#424242}.sidebar a.active,.sidebar a:hover{background:#505050}.card{background:#1e1e1e;border-color:#333;box-shadow:none}.table th,.table td{border-color:#333}input,select,textarea{background:#222;color:#e0e0e0;border-color:#555}h1,h2{color:#90caf9}.muted{color:#bbb}.tabs a{background:#222}.pill{background:#263238;color:#90caf9}}
"""

LEGACY_GENERIC_STYLE = """
body, html { margin:0; padding:0; font-family:"Tahoma",sans-serif; font-weight:300; background-color:#FFF; height:100%; }
#sidebar { width:220px; background-color:#1976D2; color:#FFF; height:100vh; position:fixed; top:0; left:0; display:flex; flex-direction:column; box-shadow:2px 0 8px rgba(0,0,0,0.2); transition:transform 0.3s ease; z-index:1200; }
@media (max-width:767px){ #sidebar{ transform:translateX(-100%); } #sidebar.open{ transform:translateX(0); } }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#FFF; padding:12px 20px; display:flex; align-items:center; gap:10px; border-bottom:1px solid rgba(255,255,255,0.1); text-decoration:none; transition:background 0.3s; font-size:0.9em; text-align:left; box-sizing:border-box; }
#sidebar .nav-icon,.logout-btn .nav-icon,.logout-btn-mobile .nav-icon,.admin-only .nav-icon { width:20px; display:inline-flex; justify-content:center; flex:0 0 20px; }
#sidebar .nav-label,.logout-btn .nav-label,.logout-btn-mobile .nav-label,.admin-only .nav-label { min-width:0; }
#sidebar a:hover,#sidebar a.active{ background-color:#1565C0; }
.logout-btn{ background-color:#C62828; border:none; cursor:pointer; margin-top:auto; transition:background-color 0.3s; }
.logout-btn-mobile{ background-color:#C62828; border:none; cursor:pointer; transition:background-color 0.3s; display:none; }
@media(max-width:767px){ .logout-btn{ display:none; } .logout-btn-mobile{ display:flex; } }
#mobile-header{ display:flex; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; }
@media(min-width:768px){ #mobile-header{ display:none; } }
.card{ background:#FFF; padding:16px; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; }
.grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:16px; }
.actions{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
.table{ width:100%; border-collapse:collapse; }
.table th,.table td{ border-bottom:1px solid #EEE; padding:10px; text-align:left; vertical-align:top; }
.button,button{ background:#1976D2; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; gap:8px; font-family:inherit; }
.button:hover,button:hover{ background:#1565C0; text-decoration:none; }
.danger{ background:#C62828; }
.danger:hover{ background:#B71C1C; }
.muted{ color:#777; }
input,select,textarea{ box-sizing:border-box; padding:10px; border:1px solid #DDD; border-radius:4px; background:#FFF; color:#000; font-family:inherit; }
label{ display:block; margin-bottom:10px; }
.flash{ padding:12px 14px; border-radius:8px; margin-bottom:14px; }
.success{ background:#E6F4EA; border:1px solid #CEEAD6; color:#137333; }
.error{ background:#FCE8E6; border:1px solid #F6AEA9; color:#A50E0E; }
.tabs{ display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }
.tabs a{ padding:10px 14px; background:#F5F5F5; border-radius:5px 5px 0 0; text-decoration:none; }
.tabs a.active{ background:#1976D2; color:#FFF; }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.card{ border:1px solid #333; background-color:#1E1E1E; box-shadow:none; }
.table th,.table td{ border-color:#333; }
input,select,textarea{ background:#333; border-color:#444; color:#FFF; }
.button,button{ background:#BB86FC; color:#000; }
.button:hover,button:hover{ background:#A370F7; }
.danger{ background:#CF6679; color:#000; }
.muted{ color:#AAA; }
.tabs a{ background:#222; }
}
"""


def page(title, body, active="dashboard", user=None, status=200):
    if user is None:
        user = current_user()
    flashes = "".join(f'<div class="flash {h(cat)}">{h(msg)}</div>' for cat, msg in session.pop("_flashes", []))
    response = legacy_page(title, legacy_user_context(user), active, LEGACY_GENERIC_STYLE, flashes + body)
    response.status_code = status
    return response


def module_page(title, body, active="endpoints", user=None, status=200):
    flashes = "".join(f'<div class="flash {h(cat)}">{h(msg)}</div>' for cat, msg in session.pop("_flashes", []))
    return Response(
        render_template_string(
            """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title><style>{{ css }}</style></head><body><main class="content" style="margin-left:0;width:100%">{{ flashes|safe }}{{ body|safe }}</main></body></html>""",
            title=title,
            css=BASE_CSS,
            flashes=flashes,
            body=body,
        ),
        status=status,
        mimetype="text/html",
    )


def simple_form(title, fields, action, values=None, submit="Save", extra=""):
    values = values or {}
    controls = []
    for name, label, kind, options in fields:
        value = values.get(name, "")
        if kind == "textarea":
            controls.append(f'<label>{h(label)}<textarea name="{h(name)}">{h(value)}</textarea></label>')
        elif kind == "select":
            opts = "".join(
                f'<option value="{h(opt)}"{" selected" if str(opt)==str(value) else ""}>{h(text)}</option>'
                for opt, text in options
            )
            controls.append(f'<label>{h(label)}<select name="{h(name)}">{opts}</select></label>')
        elif kind == "checkbox":
            controls.append(f'<label><input type="checkbox" name="{h(name)}" value="1"{" checked" if truthy(value) else ""}> {h(label)}</label>')
        else:
            controls.append(f'<label>{h(label)}<input type="{h(kind)}" name="{h(name)}" value="{h(value)}"></label>')
    return f'<h1>{h(title)}</h1><form method="post" enctype="multipart/form-data" action="{h(action)}" class="card grid">{"".join(controls)}{extra}<div class="actions"><button type="submit"><i class="fa-solid fa-save"></i>{h(submit)}</button></div></form>'


def alias(rule, endpoint=None, **options):
    def decorator(func):
        route_endpoint = endpoint or f"{func.__name__}_{re.sub(r'[^A-Za-z0-9_]+', '_', rule).strip('_') or 'root'}"
        app.route(rule, endpoint=route_endpoint, **options)(func)
        return func
    return decorator


def load_html_document(path):
    return path.read_text(encoding="utf-8")


def show_online_docs_on_error_page():
    try:
        data = settings()
    except Exception:
        return True
    return data.get("show_online_docs", "1") == "1"


def stash_traceback(exc):
    if not APP_DEBUG:
        return ""
    trace_id = uuid.uuid4().hex
    source_exc = getattr(exc, "original_exception", None) or exc
    TRACEBACKS[trace_id] = "".join(traceback.format_exception(type(source_exc), source_exc, source_exc.__traceback__))
    return trace_id


def render_error_document(filename, status_code, exc=None):
    path = WEB_ERROR_DIR / filename
    if not path.is_file():
        return Response(f"{status_code}", status=status_code, mimetype="text/plain")
    html_doc = load_html_document(path)
    if filename == "500.html":
        docs_paragraph = "<p>If this issue persists, ensure the server is up-to-date and consult the online documentation for troubleshooting, and debuging information.</p>"
        if not show_online_docs_on_error_page():
            html_doc = html_doc.replace(docs_paragraph, "")
        trace_link_markup = '<a href="/"><i class="fa-solid fa-bug"></i> Show Traceback</a>'
        trace_id = stash_traceback(exc) if exc is not None else ""
        if trace_id:
            html_doc = html_doc.replace(
                trace_link_markup,
                f'<a href="/__traceback__/{h(trace_id)}" target="_blank" rel="noopener"><i class="fa-solid fa-bug"></i> Show Traceback</a>',
            )
        else:
            html_doc = html_doc.replace(trace_link_markup, "")
    return Response(html_doc, status=status_code, mimetype="text/html")


def endpoint_output_capable(endpoint):
    if endpoint.get("output_capable") is False:
        return False
    direction = (str(endpoint.get("direction") or "") + " " + str(endpoint.get("input_type") or "")).lower()
    if "output" in direction:
        return True
    capabilities = endpoint.get("capabilities") or []
    return isinstance(capabilities, list) and ("output" in capabilities or "bells" in capabilities)


def endpoint_is_available(endpoint):
    if not endpoint_output_capable(endpoint):
        return False
    return str(endpoint.get("status") or "").strip().lower() in {"online", "configured", "ready", "ok", "up"}


def group_member_tokens(members):
    return [token for token in re.split(r"[\s,]+", str(members or "")) if token]


def endpoint_availability_map(endpoint_payload):
    availability = {}
    if not isinstance(endpoint_payload, dict):
        return availability
    for module_info in endpoint_payload.get("modules") or []:
        module_name = str(module_info.get("module") or "").strip()
        if not module_name:
            continue
        for endpoint in module_info.get("endpoints") or []:
            endpoint_id = str(endpoint.get("id") or "").strip()
            if endpoint_id:
                availability[f"{module_name}/{endpoint_id}"] = endpoint_is_available(endpoint)
    return availability


def safe_module_name(value):
    return re.fullmatch(r"[A-Za-z0-9_-]+", str(value or "")) is not None


def load_endpoint_web(module):
    if not safe_module_name(module):
        abort(400)
    path = ENDPOINT_MODULES_DIR / module / "web.py"
    root = ENDPOINT_MODULES_DIR.resolve()
    resolved = path.resolve()
    if root not in resolved.parents or not resolved.is_file():
        abort(404)
    spec = importlib.util.spec_from_file_location(f"endpoint_module_web_{module}", resolved)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def endpoint_module_xml_text(root, tag, default=""):
    node = root.find(tag)
    return (node.text or "").strip() if node is not None and node.text is not None else default


def endpoint_module_info_from_dir(module_dir):
    module = module_dir.name
    info = {
        "module": module,
        "name": module,
        "description": "",
        "input_type": "Output",
        "version": "",
        "has_settings_page": (module_dir / "web-settings.py").is_file() or (module_dir / "settings.php").is_file(),
        "has_forms": (module_dir / "endpoint-forms" / "forms.py").is_file() or (module_dir / "endpoint-forms" / "forms.php").is_file(),
    }
    info_path = module_dir / "info.xml"
    if info_path.is_file():
        try:
            import xml.etree.ElementTree as et

            parsed = et.parse(info_path).getroot()
            info["name"] = endpoint_module_xml_text(parsed, "name", module) or module
            info["description"] = endpoint_module_xml_text(parsed, "desp") or endpoint_module_xml_text(parsed, "description")
            info["input_type"] = endpoint_module_xml_text(parsed, "type", "Output") or "Output"
            info["version"] = endpoint_module_xml_text(parsed, "version")
        except Exception:
            pass
    return info


def discovered_endpoint_modules():
    modules = {}
    if ENDPOINT_MODULES_DIR.exists():
        root = ENDPOINT_MODULES_DIR.resolve()
        for module_dir in ENDPOINT_MODULES_DIR.iterdir():
            if not module_dir.is_dir() or not safe_module_name(module_dir.name):
                continue
            resolved = module_dir.resolve()
            if root not in resolved.parents and resolved != root:
                continue
            if not (module_dir / "index.py").is_file():
                continue
            modules[module_dir.name] = endpoint_module_info_from_dir(module_dir)
    return dict(sorted(modules.items(), key=lambda item: (item[1]["name"].lower(), item[0].lower())))


def ensure_endpoint_module_state_table():
    execute(
        "CREATE TABLE IF NOT EXISTS endpointmodulesloaded (`dir` VARCHAR(100) NOT NULL, enabled VARCHAR(10) NOT NULL DEFAULT 'true', PRIMARY KEY (`dir`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
    )


def endpoint_module_state_map(modules=None):
    modules = modules or discovered_endpoint_modules()
    ensure_endpoint_module_state_table()
    rows = query_all("SELECT `dir`, enabled FROM endpointmodulesloaded")
    states = {}
    for row in rows:
        module_name = str(row.get("dir") or "")
        if safe_module_name(module_name):
            states[module_name] = str(row.get("enabled") or "").strip().lower() == "true"
    if not rows:
        for module_name in modules:
            states[module_name] = True
    else:
        for module_name in modules:
            states.setdefault(module_name, False)
    return states


def endpoint_module_catalog():
    local_modules = discovered_endpoint_modules()
    states = endpoint_module_state_map(local_modules)
    payload = endpoint_ipc("LIST_ENDPOINT_MODULES")
    modules = {}
    for module_name, module_info in local_modules.items():
        merged = dict(module_info)
        merged["enabled"] = bool(states.get(module_name))
        merged["loaded"] = False
        modules[module_name] = merged
    if isinstance(payload, dict):
        for module_info in payload.get("modules") or []:
            module_name = str(module_info.get("module") or "")
            if not safe_module_name(module_name):
                continue
            merged = dict(modules.get(module_name) or {})
            merged.update(module_info)
            if module_name in local_modules:
                merged["has_settings_page"] = local_modules[module_name]["has_settings_page"]
                merged["has_forms"] = local_modules[module_name]["has_forms"]
                merged["name"] = merged.get("name") or local_modules[module_name]["name"]
                merged["description"] = merged.get("description") or local_modules[module_name]["description"]
                merged["version"] = merged.get("version") or local_modules[module_name]["version"]
            merged["module"] = module_name
            merged["enabled"] = bool(merged.get("enabled", states.get(module_name)))
            merged["loaded"] = bool(merged.get("loaded"))
            modules[module_name] = merged
    return dict(sorted(modules.items(), key=lambda item: (str(item[1].get("name") or item[0]).lower(), item[0].lower())))


@app.errorhandler(403)
def forbidden(_exc):
    return render_error_document("403.html", 403)


@app.errorhandler(404)
def not_found(_exc):
    return render_error_document("404.html", 404)


@app.errorhandler(500)
def internal_error(exc):
    return render_error_document("500.html", 500, exc)


@app.route("/__traceback__/<trace_id>")
def traceback_view(trace_id):
    if not APP_DEBUG:
        abort(404)
    trace_text = TRACEBACKS.get(str(trace_id or ""))
    if not trace_text:
        abort(404)
    return Response(trace_text, mimetype="text/plain")


@app.route("/assets/<path:filename>")
def bundled_asset(filename):
    safe_path = (WEB_STATIC_DIR / filename).resolve()
    root = WEB_STATIC_DIR.resolve()
    if root not in safe_path.parents and safe_path != root:
        abort(404)
    if not safe_path.is_file():
        abort(404)
    return send_from_directory(WEB_STATIC_DIR, filename)


@app.route("/", methods=["GET", "POST"])
@alias("/index", methods=["GET", "POST"])
def login():
    return dispatch_web_page("index")


@alias("/logout")
def logout():
    return dispatch_web_page("logout")


@alias("/dashboard")
def dashboard():
    return dispatch_web_page("dashboard")


@app.route("/oobe/", methods=["GET", "POST"])
@alias("/oobe/index", methods=["GET", "POST"])
def oobe():
    return dispatch_web_page("oobe/index")


@app.route("/history/")
@alias("/history/index")
def history():
    return dispatch_web_page("history/index")


def audio_files():
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    return sorted([p.name for p in ASSET_DIR.iterdir() if p.is_file() and p.suffix.lower() in {".wav", ".mp3", ".ogg"}], key=str.lower)


def next_message_id():
    rows = query_all("SELECT messageid FROM messages ORDER BY messageid ASC")
    used = {int(row["messageid"]) for row in rows if row.get("messageid") is not None}
    candidate = 1
    while candidate in used:
        candidate += 1
    return candidate


def message_fields(values=None):
    values = values or {}
    afiles = [("", "None")] + [(name, name) for name in audio_files()]
    return [
        ("name", "Name", "text", None),
        ("type", "Type", "select", [("text", "Text"), ("audio", "Audio"), ("text+audio", "Text + Audio"), ("liveaudio", "Live Page"), ("liveaudio+text", "Live Page + Text")]),
        ("shortmessage", "Short Message", "text", None),
        ("longmessage", "Long Message", "textarea", None),
        ("audio", "Audio File", "select", afiles),
        ("image", "Image", "text", None),
        ("color", "Color", "text", None),
        ("icon", "Icon", "text", None),
        ("expires", "Expires Rule", "text", None),
        ("priority", "Priority", "select", [("Low", "Low"), ("Normal", "Normal"), ("High", "High"), ("Emergency", "Emergency")]),
    ]


@app.route("/messages/")
@alias("/messages/index")
def messages_index():
    return dispatch_web_page("messages/index")


@alias("/messages/new", methods=["GET", "POST"])
def messages_new():
    return dispatch_web_page("messages/new")


@alias("/messages/edit", methods=["GET", "POST"])
def messages_edit():
    return dispatch_web_page("messages/edit")


def create_broadcast(message_id, groups, sender):
    sys.path.insert(0, str(BASE_DIR))
    from broadcasts import create_broadcast_from_template, expire_broadcasts_triggered_by_template, expire_message_rule_broadcasts, fetch_template

    conn = db()
    try:
        with conn.cursor() as cur:
            template = fetch_template(cur, message_id)
            if not template:
                raise RuntimeError("Message not found")
            broadcast_id, expires_rule = create_broadcast_from_template(cur, template, groups, sender)
            expire_message_rule_broadcasts(cur, expires_rule, [broadcast_id])
            expire_broadcasts_triggered_by_template(cur, message_id)
        conn.commit()
    finally:
        conn.close()


@alias("/messages/send", methods=["GET", "POST"])
def messages_send():
    return dispatch_web_page("messages/send")


@alias("/messages/custom", methods=["GET", "POST"])
def messages_custom():
    return dispatch_web_page("messages/custom")


def asset_filename(name):
    clean = secure_filename(str(name or ""))
    if not clean or clean.startswith("."):
        return ""
    return clean


def asset_path(name):
    clean = asset_filename(name)
    if not clean:
        abort(400)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    path = (ASSET_DIR / clean).resolve()
    if ASSET_DIR.resolve() not in path.parents and path != ASSET_DIR.resolve():
        abort(400)
    return path


@app.route("/assets/", methods=["GET", "POST"])
@alias("/assets/index", methods=["GET", "POST"])
def assets():
    return dispatch_web_page("assets/index")


@app.route("/paging/")
@alias("/paging/index")
def paging():
    return dispatch_web_page("paging/index")


@alias("/admin/manage-groups", methods=["GET", "POST"])
def manage_groups():
    return dispatch_web_page("admin/manage-groups")


@alias("/admin/manage-users", methods=["GET", "POST"])
def manage_users():
    return dispatch_web_page("admin/manage-users")


def endpoint_ipc(command):
    try:
        with socket.create_connection(("127.0.0.1", 50000), timeout=2) as sock:
            sock.sendall(command.encode() + b"\n")
            return json.loads(sock.recv(1024 * 1024).decode())
    except Exception as exc:
        return {"ok": False, "error": str(exc), "modules": []}


@alias("/admin/manage-endpoints")
def manage_endpoints():
    return dispatch_web_page("admin/manage-endpoints")


@alias("/admin/new-endpoint")
def new_endpoint():
    return dispatch_web_page("admin/new-endpoint")


@alias("/admin/new-endpoint-configure")
def new_endpoint_configure():
    return dispatch_web_page("admin/new-endpoint-configure")


@alias("/admin/endpoint-form-frame", methods=["GET", "POST"])
def endpoint_form_frame():
    return dispatch_web_page("admin/endpoint-form-frame")


@alias("/admin/endpoint-action-page", methods=["GET", "POST"])
@alias("/admin/endpoint-action-frame", methods=["GET", "POST"])
def endpoint_action_page():
    return dispatch_web_page("admin/endpoint-action-page")


@alias("/admin/endpoint-module-settings", methods=["GET", "POST"])
def endpoint_module_settings():
    return dispatch_web_page("admin/endpoint-module-settings")


@alias("/admin/endpoint-module-settings-configure")
@alias("/admin/endpoint-module-settings-frame")
def endpoint_module_settings_configure():
    return dispatch_web_page("admin/endpoint-module-settings-configure")


SETTINGS_TABS = "<div class='tabs'><a href='/admin/settings/general'>General</a><a href='/admin/settings/login'>Login</a><a href='/admin/settings/sip'>SIP</a><a href='/admin/settings/branding'>Branding</a><a href='/admin/settings/about'>About</a></div>"


@alias("/admin/settings/general", methods=["GET", "POST"])
def settings_general():
    return dispatch_web_page("admin/settings/general")


@alias("/admin/settings/login", methods=["GET", "POST"])
def settings_login():
    return dispatch_web_page("admin/settings/login")


@alias("/admin/settings/branding", methods=["GET", "POST"])
def settings_branding():
    return dispatch_web_page("admin/settings/branding")


@alias("/admin/settings/sip", methods=["GET", "POST"])
def settings_sip():
    return dispatch_web_page("admin/settings/sip")


def read_version():
    try:
        for line in (BASE_DIR / "pyproject.toml").read_text().splitlines():
            if line.strip().startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


@alias("/admin/settings/about")
def settings_about():
    return dispatch_web_page("admin/settings/about")


def ensure_bell_schema():
    execute_many(
        [
            ("CREATE TABLE IF NOT EXISTS bell_schedules (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL, enabled TINYINT(1) NOT NULL DEFAULT 1, created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP, timezone VARCHAR(64) NOT NULL DEFAULT 'server')", ()),
            ("CREATE TABLE IF NOT EXISTS bell_lists (id INT AUTO_INCREMENT PRIMARY KEY, schedule_id INT NOT NULL DEFAULT 0, name VARCHAR(100) NOT NULL, created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP)", ()),
            ("CREATE TABLE IF NOT EXISTS bell_events (id INT AUTO_INCREMENT PRIMARY KEY, list_id INT NOT NULL, fire_time TIME NOT NULL, audio TEXT NOT NULL, days_of_week VARCHAR(32) NOT NULL DEFAULT '0,1,2,3,4,5,6')", ()),
            ("CREATE TABLE IF NOT EXISTS bell_schedule_groups (schedule_id INT NOT NULL, group_id VARCHAR(100) NOT NULL, PRIMARY KEY (schedule_id, group_id))", ()),
            ("CREATE TABLE IF NOT EXISTS bell_calendar_lists (schedule_id INT NOT NULL, bell_date DATE NOT NULL, list_id INT NOT NULL, PRIMARY KEY (schedule_id, bell_date, list_id))", ()),
        ]
    )


@app.route("/bells/")
@alias("/bells/index")
def bells_index():
    return dispatch_web_page("bells/index")


@alias("/bells/time")
def bells_time():
    return dispatch_web_page("bells/time")


@alias("/bells/new", methods=["GET", "POST"])
def bells_new():
    return dispatch_web_page("bells/new")


@alias("/bells/edit", methods=["GET", "POST"])
def bells_edit():
    return dispatch_web_page("bells/edit")


@alias("/bells/lists", methods=["GET", "POST"])
@alias("/bells/bell-lists", methods=["GET", "POST"])
def bells_lists():
    return dispatch_web_page("bells/lists")


@alias("/bells/groups", methods=["GET", "POST"])
def bells_groups():
    return dispatch_web_page("bells/groups")


@alias("/bells/calendar")
def bells_calendar():
    return dispatch_web_page("bells/calendar")


@alias("/bells/devices")
def bells_devices():
    return dispatch_web_page("bells/devices")


@alias("/admin/delete-endpoint")
def delete_endpoint_redirect():
    return dispatch_web_page("admin/delete-endpoint")


@alias("/admin/edit-endpoint")
def edit_endpoint_redirect():
    return dispatch_web_page("admin/edit-endpoint")


if __name__ == "__main__":
    app.run("0.0.0.0", int(os.getenv("PORT", "8080")))
