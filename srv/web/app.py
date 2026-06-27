import hashlib
import hmac
import html
import importlib.util
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pymysql
try:
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerificationError
except ImportError:
    PasswordHasher = None
    InvalidHashError = VerificationError = Exception
import endpoints
from clientd import (
    DESKTOP_CLIENT_HEADER,
    desktop_member_user_id,
    build_desktop_token,
    desktop_member_token,
    fetch_active_broadcast,
    first_audio_name,
    groups_for_user as desktop_groups_for_user,
    is_desktop_member_token,
    normalize_color,
    product_name as desktop_product_name,
    user_has_connected_client,
    user_in_broadcast,
    verify_desktop_token,
)
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
DEMO_MODE_HTML_PATH = WEB_ROOT_DIR / "demomode.html"
ENDPOINT_MODULES_DIR = endpoints.MODULE_STORE_DIR
ASSET_DIR = Path(os.getenv("ASSET_PATH", "/var/lib/openpagingserver/assets"))
load_dotenv(BASE_DIR / ".env")

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
APP_DEBUG = str(os.getenv("DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}
TRACEBACKS = {}
API_TOKEN_LABEL_LENGTH = 120
ENDPOINT_IPC_TIMEOUT = max(2.0, float(os.getenv("OPS_ENDPOINT_IPC_TIMEOUT", "5")))
API_TOKEN_HASHER = PasswordHasher() if PasswordHasher is not None else None
MESSAGE_VENDOR_SCHEMA_READY = False
WEB_RATE_LIMIT_BUCKETS = {}
WEB_RATE_LIMIT_LOCK = threading.Lock()
WEB_RATE_LIMIT_EXEMPT_PREFIXES = ("/bundled-assets/", "/assets/file/", "/favicon.ico")

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
DEMO_MODE = str(os.getenv("DEMO_MODE", "")).strip().lower() in {"1", "true", "yes", "on"}


def int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def client_ip():
    return str(request.remote_addr or "unknown")


def rate_limit_exceeded(scope, key, limit, window_seconds, buckets=WEB_RATE_LIMIT_BUCKETS, lock=WEB_RATE_LIMIT_LOCK):
    if limit <= 0 or window_seconds <= 0:
        return False, 0
    now = time.monotonic()
    bucket_key = (scope, str(key or "unknown"))
    with lock:
        bucket = buckets.setdefault(bucket_key, deque())
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(window_seconds - (now - bucket[0])))
            return True, retry_after
        bucket.append(now)
    return False, 0


def rate_limited_response(retry_after):
    response = Response("Too many requests. Please wait and try again.", status=429, mimetype="text/plain")
    response.headers["Retry-After"] = str(max(1, int(retry_after or 1)))
    return response


def check_rate_limit(scope, key, limit, window_seconds):
    limited, retry_after = rate_limit_exceeded(scope, key, limit, window_seconds)
    if limited:
        return rate_limited_response(retry_after)
    return None


@app.before_request
def enforce_web_rate_limits():
    if str(os.getenv("WEB_RATE_LIMIT_ENABLE", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return None
    path = request.path or ""
    if any(path.startswith(prefix) for prefix in WEB_RATE_LIMIT_EXEMPT_PREFIXES):
        return None
    ip_key = client_ip()
    checks = [
        ("web-ip-minute", ip_key, int_env("WEB_RATE_LIMIT_IP_PER_MINUTE", 240), 60),
        ("web-ip-hour", ip_key, int_env("WEB_RATE_LIMIT_IP_PER_HOUR", 3000), 3600),
    ]
    user_id = session.get("user_id")
    if user_id not in (None, ""):
        checks.append(("web-user-minute", user_id, int_env("WEB_RATE_LIMIT_USER_PER_MINUTE", 300), 60))
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        checks.append(("web-post-ip-minute", ip_key, int_env("WEB_RATE_LIMIT_POST_IP_PER_MINUTE", 60), 60))
        if user_id not in (None, ""):
            checks.append(("web-post-user-minute", user_id, int_env("WEB_RATE_LIMIT_POST_USER_PER_MINUTE", 45), 60))
    for scope, key, limit, window_seconds in checks:
        response = check_rate_limit(scope, key, limit, window_seconds)
        if response is not None:
            return response
    return None


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


def ensure_message_vendor_schema():
    global MESSAGE_VENDOR_SCHEMA_READY
    if MESSAGE_VENDOR_SCHEMA_READY:
        return
    statements = []
    message_columns = table_columns("messages")
    if "vendor_specific" not in message_columns:
        statements.append(("ALTER TABLE messages ADD COLUMN vendor_specific TEXT DEFAULT NULL", ()))
    broadcast_columns = table_columns("broadcasts")
    if "vendor_specific" not in broadcast_columns:
        statements.append(("ALTER TABLE broadcasts ADD COLUMN vendor_specific TEXT DEFAULT NULL", ()))
    else:
        statements.append(("ALTER TABLE broadcasts MODIFY COLUMN vendor_specific TEXT DEFAULT NULL", ()))
    if statements:
        execute_many(statements)
    MESSAGE_VENDOR_SCHEMA_READY = True


def truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def demo_mode_enabled():
    return DEMO_MODE


def api_token_hash(token):
    return hashlib.sha256(str(token or "").encode()).hexdigest()


def create_api_token_value():
    return "usr_" + secrets.token_urlsafe(32)


def hash_api_token_value(token):
    if API_TOKEN_HASHER is None:
        raise RuntimeError("Argon2 support is not installed. Install dependencies from requirements.txt.")
    return API_TOKEN_HASHER.hash(str(token or ""))


def verify_api_token_value(token, stored_hash):
    token = str(token or "")
    stored_hash = str(stored_hash or "")
    if not token or not stored_hash:
        return False
    if stored_hash.startswith("$argon2"):
        if API_TOKEN_HASHER is None:
            return False
        try:
            return bool(API_TOKEN_HASHER.verify(stored_hash, token))
        except (VerificationError, InvalidHashError):
            return False
    return hmac.compare_digest(api_token_hash(token), stored_hash)


def ensure_api_token_schema():
    execute_many(
        [
            (
                """
                CREATE TABLE IF NOT EXISTS api_tokens (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    token_hash VARCHAR(255) NOT NULL,
                    token_prefix VARCHAR(24) DEFAULT NULL,
                    token_label VARCHAR(120) DEFAULT NULL,
                    expires_at DATETIME DEFAULT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_used_at DATETIME DEFAULT NULL,
                    UNIQUE KEY api_tokens_hash_unique (token_hash),
                    KEY api_tokens_user_idx (user_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
                """,
                (),
            ),
        ]
    )
    columns = table_columns("api_tokens")
    statements = []
    if "token_label" not in columns:
        statements.append(("ALTER TABLE api_tokens ADD COLUMN token_label VARCHAR(120) DEFAULT NULL AFTER token_prefix", ()))
    statements.append(("ALTER TABLE api_tokens MODIFY COLUMN token_hash VARCHAR(255) NOT NULL", ()))
    if "token_prefix" in columns:
        statements.append(("ALTER TABLE api_tokens MODIFY COLUMN token_prefix VARCHAR(24) DEFAULT NULL", ()))
    if statements:
        execute_many(statements)


def current_user():
    user_id = session.get("user_id")
    if user_id is None or user_id == "":
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
    demo_badge = ""
    if demo_mode_enabled():
        demo_badge = '<span class="sidebar-demo-mode"><i class="fa-solid fa-bag-shopping"></i> Demo Mode</span>'
    if not use_logo or not light_logo:
        return f'<div class="sidebar-brand"><div class="sidebar-brand-inner"><span>{h(product_name)}</span>{demo_badge}</div></div>'
    dark_source = f'<source media="(prefers-color-scheme: dark)" srcset="{h(dark_logo)}">' if dark_logo else ""
    return (
        '<div class="sidebar-brand sidebar-brand-logo"><div class="sidebar-brand-inner"><picture>'
        f'{dark_source}<img src="{h(light_logo)}" alt="{h(product_name)}">'
        f"</picture>{demo_badge}</div></div>"
    )


def demo_mode_iframe_html(topic=""):
    if DEMO_MODE_HTML_PATH.is_file():
        return Response(DEMO_MODE_HTML_PATH.read_text(encoding="utf-8"), mimetype="text/html")
    return Response('<!DOCTYPE html><html lang="en"><body><p>Demo Mode</p></body></html>', mimetype="text/html")


def demo_mode_page(title, ctx, active, topic):
    iframe_src = "/demo-mode?" + urlencode({"topic": topic})
    content = f"""<h1>{h(title)}</h1>
    <div class="card" style="padding:0; overflow:hidden;">
        <iframe src="{h(iframe_src)}" style="width:100%; min-height:260px; border:0; border-radius:12px; display:block;" title="Demo Mode"></iframe>
    </div>"""
    return legacy_page(title, ctx, active, LEGACY_GENERIC_STYLE, content)


def demo_mode_inline_notice(topic):
    iframe_src = "/demo-mode?" + urlencode({"topic": topic})
    return f"""<div class="card" style="padding:0; overflow:hidden; margin-bottom:16px;">
        <iframe src="{h(iframe_src)}" style="width:100%; min-height:240px; border:0; border-radius:12px; display:block;" title="Demo Mode"></iframe>
    </div>"""


def legacy_user_context(user=None):
    if user is None:
        user = current_user()
    user = user or {}
    role = user.get("role") or ""
    if user.get("id") is not None and user.get("id") != "" and not role:
        role_row = query_one("SELECT role FROM users WHERE id = %s LIMIT 1", (user.get("id"),)) or {}
        role = role_row.get("role") or ""
    data = settings()
    product_name = data.get("product_name") or "Open Paging Server"
    return {
        "user": user,
        "role": role,
        "username": user.get("username") or session.get("username") or "User",
        "is_admin": role in {"admin", "tempadmin"},
        "is_receiver": role in {"receiver", "tempreceiver"},
        "settings": data,
        "product_name": product_name,
        "favicon": data.get("favicon") or "",
        "show_online_docs": data.get("show_online_docs", "1"),
        "brand_html": ops_sidebar_brand_html(data, product_name),
    }


def legacy_sidebar_html(ctx, active):
    user_settings_class = ' class="active user-settings-link"' if active == "user-settings" else ' class="user-settings-link"'
    user_settings_link = f'<a href="/user/settings"{user_settings_class}><span class="nav-icon"><i class="fa-solid fa-user"></i></span><span class="nav-label">{h(ctx.get("username") or "User")}</span></a>'
    logout_button = '<button class="logout-btn" onclick="logout()"><span class="nav-icon"><i class="fa-solid fa-sign-out-alt"></i></span><span class="nav-label">Logout</span></button>'
    links = [
        ("/dashboard", "house", "Dashboard", "dashboard"),
        ("/paging/", "bullhorn", "Paging", "paging"),
        ("/messages/", "message", "Messages", "messages"),
        ("/history/", "clock-rotate-left", "History", "history"),
        ("/bells/", "bell", "Bells", "bells"),
        ("/assets/", "folder-open", "Assets", "assets"),
    ]
    nav_links = []
    for href, icon, label, key in links:
        cls = ' class="active"' if active == key else ""
        nav_links.append(
            f'<a href="{h(href)}"{cls}><span class="nav-icon"><i class="fa-solid fa-{icon}"></i></span><span class="nav-label">{h(label)}</span></a>'
        )
    if ctx.get("is_admin"):
        admin_links = [
            ("/admin/manage-users", "users-cog", "Manage Users", "users"),
            ("/admin/manage-endpoints", "shapes", "Manage Endpoints", "endpoints"),
            ("/admin/manage-groups", "user-group", "Manage Groups", "groups"),
            ("/admin/settings/general", "cogs", "Server Settings", "settings"),
        ]
        for href, icon, label, key in admin_links:
            cls = "admin-only active" if active == key else "admin-only"
            nav_links.append(
                f'<a href="{h(href)}" class="{cls}"><span class="nav-icon"><i class="fa-solid fa-{icon}"></i></span><span class="nav-label">{h(label)}</span></a>'
            )
    if ctx.get("show_online_docs") == "1":
        nav_links.append(
            '<a href="https://docs.openpagingserver.org"><span class="nav-icon"><i class="fa-solid fa-book"></i></span><span class="nav-label">Online Documentation</span></a>'
        )
    rendered = [
        ctx["brand_html"],
        '<div class="sidebar-nav">',
        *nav_links,
        "</div>",
        '<div class="sidebar-account">',
        user_settings_link,
        logout_button,
        '<div class="mobile-nav-divider"></div>',
        "</div>",
    ]
    return "\n    ".join(rendered)


def legacy_page(title, ctx, active, style, content, extra_script="", extra_after=""):
    favicon_html = '<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">'
    common_sidebar_style = """
#sidebar a,.logout-btn,.admin-only{display:flex!important;align-items:center;gap:10px}
#sidebar .nav-icon,.logout-btn .nav-icon,.admin-only .nav-icon{width:20px;display:inline-flex;justify-content:center;flex:0 0 20px}
#sidebar .nav-label,.logout-btn .nav-label,.admin-only .nav-label{min-width:0}
#sidebar a i,.logout-btn i,.admin-only i{margin-right:0!important;width:auto!important;text-align:center}
.logout-btn{display:flex!important;width:100%}
.logout-btn-mobile{display:none!important}
.sidebar-nav{display:flex;flex-direction:column}
.sidebar-account{display:flex;flex-direction:column;margin-top:auto}
.mobile-nav-divider{display:none}
@media (max-width:767px){.sidebar-account{margin-top:0;order:0}.sidebar-nav{order:1}.mobile-nav-divider{display:block;height:1px;background:#000;margin:0}}
"""
    demo_markup = ""
    demo_script = ""
    if demo_mode_enabled():
        demo_markup = """
<div id="demo-mode-overlay" onclick="closeDemoModePopupOnOverlay(event)" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.72); z-index:2500; align-items:center; justify-content:center; padding:20px; box-sizing:border-box;">
    <button type="button" onclick="closeDemoModePopup()" aria-label="Close demo mode popup" style="position:fixed; top:12px; right:12px; width:42px; height:42px; border:none; border-radius:50%; background:transparent; color:#FFF; cursor:pointer; z-index:2502; font-size:22px;"><i class="fa-solid fa-xmark"></i></button>
    <div style="position:relative; width:min(720px, 100%);">
        <iframe id="demo-mode-frame" src="/demo-mode" style="width:100%; min-height:320px; border:0; border-radius:18px; background:transparent; display:block;"></iframe>
    </div>
</div>"""
        demo_script = """
function openDemoModePopup(topic) {
  var overlay = document.getElementById('demo-mode-overlay');
  var frame = document.getElementById('demo-mode-frame');
  if (!overlay || !frame) return;
  frame.src = '/demo-mode?topic=' + encodeURIComponent(topic || '');
  overlay.style.display = 'flex';
}
function closeDemoModePopup() {
  var overlay = document.getElementById('demo-mode-overlay');
  if (overlay) overlay.style.display = 'none';
}
function closeDemoModePopupOnOverlay(event) {
  if (event && event.target && event.target.id === 'demo-mode-overlay') closeDemoModePopup();
}
document.addEventListener('keydown', function(event) {
  if (event.key === 'Escape') closeDemoModePopup();
});
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
{demo_script}
{extra_script}
</script>
{demo_markup}
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
.md-checkbox-container{display:flex;align-items:center;position:relative;cursor:pointer;font-size:14px;font-weight:500;color:#222;user-select:none;gap:12px}.md-checkbox-container input{position:absolute;opacity:0;cursor:pointer;height:0;width:0}.md-checkmark{position:relative;display:inline-block;flex:0 0 auto;height:20px;width:20px;background:#fff;border:2px solid #5f6368;border-radius:2px;transition:all .2s}.md-checkbox-container:hover input ~ .md-checkmark{border-color:#202124}.md-checkbox-container input:checked ~ .md-checkmark{background:#1976D2;border-color:#1976D2}.md-checkmark:after{content:"";position:absolute;display:none;left:6px;top:2px;width:4px;height:10px;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}.md-checkbox-container input:checked ~ .md-checkmark:after{display:block}
@media(max-width:767px){.layout{display:block}.sidebar{position:static;width:100%;min-height:auto}.content{margin-left:0;width:100%;padding:16px}.table{display:block;overflow-x:auto}}
@media(prefers-color-scheme:dark){body,html{background:#121212;color:#e0e0e0}.sidebar{background:#424242}.sidebar a.active,.sidebar a:hover{background:#505050}.card{background:#1e1e1e;border-color:#333;box-shadow:none}.table th,.table td{border-color:#333}input,select,textarea{background:#222;color:#e0e0e0;border-color:#555}h1,h2{color:#90caf9}.muted{color:#bbb}.tabs a{background:#222}.pill{background:#263238;color:#90caf9}.md-checkbox-container{color:#E0E0E0}.md-checkmark{border-color:#9AA0A6;background:#1E1E1E}.md-checkbox-container:hover input ~ .md-checkmark{border-color:#E8EAED}.md-checkbox-container input:checked ~ .md-checkmark{background:#8AB4F8;border-color:#8AB4F8}.md-checkmark:after{border-color:#1E1E1E}}
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
.md-checkbox-container{display:flex;align-items:center;position:relative;cursor:pointer;font-size:14px;font-weight:500;color:#202124;user-select:none;gap:12px}.md-checkbox-container input{position:absolute;opacity:0;cursor:pointer;height:0;width:0}.md-checkmark{position:relative;display:inline-block;flex:0 0 auto;height:20px;width:20px;background:#FFF;border:2px solid #5f6368;border-radius:2px;transition:all .2s}.md-checkbox-container:hover input ~ .md-checkmark{border-color:#202124}.md-checkbox-container input:checked ~ .md-checkmark{background:#1976D2;border-color:#1976D2}.md-checkmark:after{content:"";position:absolute;display:none;left:6px;top:2px;width:4px;height:10px;border:solid #FFF;border-width:0 2px 2px 0;transform:rotate(45deg)}.md-checkbox-container input:checked ~ .md-checkmark:after{display:block}
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
.md-checkbox-container{color:#E0E0E0;}
.md-checkmark{border-color:#9AA0A6;background:#1E1E1E;}
.md-checkbox-container:hover input ~ .md-checkmark{border-color:#E8EAED;}
.md-checkbox-container input:checked ~ .md-checkmark{background:#8AB4F8;border-color:#8AB4F8;}
.md-checkmark:after{border-color:#1E1E1E;}
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
            controls.append(f'<label class="md-checkbox-container"><input type="checkbox" name="{h(name)}" value="1"{" checked" if truthy(value) else ""}><span class="md-checkmark"></span><span>{h(label)}</span></label>')
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
    if "available" in endpoint:
        return bool(endpoint.get("available"))
    return str(endpoint.get("status") or "").strip().lower() in {"online", "configured", "ready", "ok", "up"}


def group_member_tokens(members):
    return [token for token in re.split(r"[\s,]+", str(members or "")) if token]


def desktop_eligible_users():
    return query_all(
        """
        SELECT id, username, role
        FROM users
        WHERE role IS NULL OR role = '' OR role NOT IN ('receiver', 'tempreceiver')
        ORDER BY username ASC
        """
    )


def desktop_user_choices():
    rows = desktop_eligible_users()
    return [
        {
            "value": desktop_member_token(row.get("id")),
            "label": str(row.get("username") or ""),
        }
        for row in rows
        if str(row.get("id") if row.get("id") is not None else "").strip()
    ]


def group_member_available(member, endpoint_availability):
    token = str(member or "").strip()
    if not token:
        return False
    if is_desktop_member_token(token):
        return user_has_connected_client(desktop_member_user_id(token))
    return bool(endpoint_availability.get(token))


def any_desktop_recipient_available():
    return any(user_has_connected_client(row.get("id")) for row in desktop_eligible_users())


def any_recipient_available(endpoint_availability):
    return any(bool(value) for value in (endpoint_availability or {}).values()) or any_desktop_recipient_available()


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
    try:
        return endpoints.load_endpoint_web_module(module)
    except FileNotFoundError:
        abort(404)
    except Exception:
        abort(404)


def safe_load_endpoint_web_module(module_name, trusted=False):
    if not trusted:
        return None, ""
    try:
        return endpoints.load_endpoint_web_module(module_name, missing_ok=True), ""
    except Exception as exc:
        return None, str(exc)


def discovered_endpoint_modules():
    modules = {}
    for module_name, package in endpoints.discover_endpoint_packages(extract_if_trusted=True).items():
        manifest = package.get("manifest") or {}
        verification = package.get("verification") or {}
        trusted = bool(package.get("trusted"))
        web_mod, web_load_error = safe_load_endpoint_web_module(module_name, trusted=trusted)
        modules[module_name] = {
            "module": module_name,
            "name": manifest.get("name") or module_name,
            "description": manifest.get("description") or "",
            "developer": manifest.get("developer") or manifest.get("author") or "",
            "input_type": manifest.get("input_type") or manifest.get("type") or "Output",
            "version": manifest.get("version") or "",
            "minimum_ops_version": manifest.get("minimum_ops_version") or endpoints.OPS_VERSION,
            "requirements": manifest.get("requirements") or [],
            "trusted": trusted,
            "can_load": trusted,
            "input_capable": endpoints.module_type_has_input(manifest.get("input_type") or manifest.get("type") or "Output"),
            "output_capable": endpoints.module_type_has_output(manifest.get("input_type") or manifest.get("type") or "Output"),
            "signature_state": verification.get("signature_state") or "unsigned",
            "signature_label": verification.get("signature_label") or "",
            "signer": verification.get("organization") or "",
            "load_error": "" if trusted else package.get("load_error") or endpoints.UNSIGNED_ERROR,
            "web_load_error": web_load_error,
            "has_settings_page": bool(getattr(web_mod, "render_settings", None)) if web_mod else False,
            "has_forms": bool(getattr(web_mod, "forms", None)) if web_mod else False,
        }
    return dict(sorted(modules.items(), key=lambda item: (item[1]["name"].lower(), item[0].lower())))


def ensure_endpoint_module_state_table():
    endpoints.ensure_module_registry_table()


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


def builtin_endpoint_modules():
    sip_web_mod, sip_web_error = safe_load_endpoint_web_module("siptrunks", trusted=True)
    multicast_web_mod, multicast_web_error = safe_load_endpoint_web_module(endpoints.MULTICAST_RTP_MODULE, trusted=True)
    return {
        "siptrunks": {
            "module": "siptrunks",
            "name": "SIP Trunks",
            "description": "Interconnect Open Paging Server with a VoIP-based PBX or ITSP",
            "developer": "Open Paging Server",
            "input_type": "Input+Output",
            "version": endpoints.OPS_VERSION,
            "enabled": True,
            "loaded": True,
            "trusted": True,
            "can_load": True,
            "system_builtin": True,
            "input_capable": True,
            "output_capable": True,
            "web_load_error": sip_web_error,
            "has_settings_page": False,
            "has_forms": bool(getattr(sip_web_mod, "forms", None)) if sip_web_mod else False,
        },
        endpoints.MULTICAST_RTP_MODULE: {
            "module": endpoints.MULTICAST_RTP_MODULE,
            "name": endpoints.MULTICAST_RTP_NAME,
            "description": endpoints.MULTICAST_RTP_DESCRIPTION,
            "developer": "Open Paging Server",
            "input_type": "Output",
            "version": endpoints.OPS_VERSION,
            "enabled": True,
            "loaded": True,
            "trusted": True,
            "can_load": True,
            "system_builtin": True,
            "input_capable": False,
            "output_capable": True,
            "web_load_error": multicast_web_error,
            "has_settings_page": False,
            "has_forms": bool(getattr(multicast_web_mod, "forms", None)) if multicast_web_mod else False,
        },
    }


def endpoint_module_catalog(include_system=False):
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
    if include_system:
        modules.update(builtin_endpoint_modules())
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


@alias("/login/basic-captcha.svg")
def login_basic_captcha():
    return dispatch_web_page("login-captcha")


@alias("/logout")
def logout():
    return dispatch_web_page("logout")


def desktop_authorized_user():
    auth_header = str(request.headers.get("Authorization") or "")
    token = auth_header.split(" ", 1)[1].strip() if auth_header.lower().startswith("bearer ") else ""
    if not token:
        token = str(request.args.get("token") or "").strip()
    return verify_desktop_token(token)


def require_desktop_client():
    if not str(request.headers.get(DESKTOP_CLIENT_HEADER) or "").strip():
        return jsonify(error="Desktop client header required"), 400
    user = desktop_authorized_user()
    if not user:
        return jsonify(error="Unauthorized"), 401
    return user


@alias("/desktop/session/login", methods=["POST"])
def desktop_session_login():
    if not str(request.headers.get(DESKTOP_CLIENT_HEADER) or "").strip():
        return jsonify(error="Desktop client header required"), 400
    payload = request.get_json(silent=True) or request.form.to_dict()
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not username or not password:
        return jsonify(error="Username and password are required."), 400
    user = query_one(
        "SELECT id, username, email, password, salt, role, accountexpire, loginsleft FROM users WHERE username=%s OR email=%s LIMIT 1",
        (username, username),
    )
    if not user:
        return jsonify(error="Invalid username or password."), 401
    expected_hash = hashlib.sha256((password + str(user.get("salt") or "")).encode()).hexdigest()
    if expected_hash != str(user.get("password") or ""):
        return jsonify(error="Invalid username or password."), 401
    expire_date = user.get("accountexpire")
    if expire_date and str(expire_date) not in {"0000-00-00", "None"} and str(expire_date) < datetime.now().strftime("%Y-%m-%d"):
        return jsonify(error="This account has expired."), 403
    token, expires_at = build_desktop_token(user)
    return jsonify(
        token=token,
        expires_at=expires_at,
        websocket_path="/desktop/ws",
        keepalive_path="/desktop/session/ping",
        product_name=desktop_product_name(),
        user={"id": user.get("id"), "username": user.get("username"), "role": user.get("role")},
        groups=desktop_groups_for_user(user.get("id")),
    )


@alias("/desktop/session/ping", methods=["GET", "OPTIONS"])
def desktop_session_ping():
    user = require_desktop_client()
    if not isinstance(user, dict):
        return user
    response = jsonify(
        ok=True,
        product_name=desktop_product_name(),
        server_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user={"id": user.get("id"), "username": user.get("username"), "role": user.get("role")},
        groups=desktop_groups_for_user(user.get("id")),
    )
    response.status_code = 200
    return response


@alias("/desktop/broadcasts/<broadcast_id>/audio")
def desktop_broadcast_audio(broadcast_id):
    user = require_desktop_client()
    if not isinstance(user, dict):
        return user
    broadcast = fetch_active_broadcast(broadcast_id)
    if not broadcast:
        abort(404)
    if not user_in_broadcast(user.get("id"), broadcast):
        abort(403)
    audio_name = first_audio_name(broadcast)
    if not audio_name:
        abort(404)
    return send_file(asset_path(audio_name), as_attachment=False, conditional=True)


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


def create_broadcast(message_id, groups, sender, overrides=None):
    sys.path.insert(0, str(BASE_DIR))
    from broadcasts import (
        create_broadcast_from_template,
        expire_broadcasts_triggered_by_template,
        expire_message_rule_broadcasts,
        fetch_template,
    )

    ensure_message_vendor_schema()
    conn = db()
    try:
        with conn.cursor() as cur:
            template = fetch_template(cur, message_id)
            if not template:
                raise RuntimeError("Message not found")
            broadcast_id, expires_rule = create_broadcast_from_template(cur, template, groups, sender, overrides=overrides)
            expire_message_rule_broadcasts(cur, expires_rule, [broadcast_id])
            expire_broadcasts_triggered_by_template(cur, message_id, [broadcast_id])
        conn.commit()
    finally:
        conn.close()


@alias("/messages/send", methods=["GET", "POST"])
def messages_send():
    return dispatch_web_page("messages/send")


@alias("/messages/custom", methods=["GET", "POST"])
def messages_custom():
    return dispatch_web_page("messages/custom")


@alias("/messages/variable-api-test", methods=["POST"])
def messages_variable_api_test():
    return dispatch_web_page("messages/variable-api-test")


def asset_filename(name):
    raw = str(name or "").replace("\0", "").replace("\\", "/").split("/")[-1].strip()
    if not raw or raw.startswith("."):
        return ""
    return raw


def asset_lookup_names(name):
    raw = asset_filename(name)
    if not raw:
        return []
    names = [raw]
    secure = secure_filename(raw)
    if secure and secure not in names:
        names.append(secure)
    return names


def asset_path(name):
    names = asset_lookup_names(name)
    if not names:
        abort(400)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    base = ASSET_DIR.resolve()
    for candidate_name in names:
        path = (base / candidate_name).resolve()
        if base not in path.parents:
            abort(400)
        if path.is_file():
            return path
    wanted = {candidate.lower() for candidate in names}
    try:
        for path in base.iterdir():
            if path.is_file() and path.name.lower() in wanted:
                return path.resolve()
    except OSError:
        pass
    path = (base / names[0]).resolve()
    if base not in path.parents:
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


@alias("/user/settings", methods=["GET", "POST"])
def user_settings():
    return dispatch_web_page("user/settings")


def endpoint_ipc(command):
    try:
        with endpoints.connect_endpoint_ipc(timeout=ENDPOINT_IPC_TIMEOUT) as sock:
            sock.sendall(command.encode() + b"\n")
            raw = endpoints.recv_line(sock)
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                return {"ok": False, "error": "Endpoint manager returned an empty response", "modules": []}
            try:
                return json.loads(text)
            except Exception:
                return {"ok": False, "error": f"Invalid endpoint manager response: {text[:200]}", "modules": []}
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


@alias("/admin/endpoint-module-settings-configure", methods=["GET", "POST"])
@alias("/admin/endpoint-module-settings-frame")
def endpoint_module_settings_configure():
    return dispatch_web_page("admin/endpoint-module-settings-configure")


SETTINGS_TABS = "<div class='tabs'><a href='/admin/settings/general'>General</a><a href='/admin/settings/login'>Login</a><a href='/admin/settings/sip'>SIP</a><a href='/admin/settings/branding'>Branding</a><a href='/admin/settings/about'>About</a></div>"


@alias("/admin/settings/general", methods=["GET", "POST"])
def settings_general():
    return dispatch_web_page("admin/settings/general")


@alias("/admin/settings/multicast-gateway", methods=["GET", "POST"])
def settings_multicast_gateway():
    return dispatch_web_page("admin/settings/multicast-gateway")


@alias("/admin/settings/login", methods=["GET", "POST"])
def settings_login():
    return dispatch_web_page("admin/settings/login")


@alias("/admin/settings/branding", methods=["GET", "POST"])
def settings_branding():
    return dispatch_web_page("admin/settings/branding")


@alias("/admin/settings/sip", methods=["GET", "POST"])
def settings_sip():
    return dispatch_web_page("admin/settings/sip")


@alias("/admin/sip-dns-check")
def admin_sip_dns_check():
    return dispatch_web_page("admin/sip-dns-check")


@alias("/admin/settings/web", methods=["GET", "POST"])
def settings_web():
    return dispatch_web_page("admin/settings/web")


@alias("/admin/settings/api", methods=["GET", "POST"])
def settings_api():
    return dispatch_web_page("admin/settings/api")


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


@alias("/demo-mode")
def demo_mode_info():
    return demo_mode_iframe_html(request.args.get("topic", ""))


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


@alias("/bells/calendar", methods=["GET", "POST"])
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
