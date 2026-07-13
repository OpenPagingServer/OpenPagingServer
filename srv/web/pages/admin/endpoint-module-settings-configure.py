from srv.web.app import *

MODULE_CONFIGURE_STYLE = r"""
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
#content h1{ font-weight:400; margin-bottom:4px; }
.header-actions { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:20px; gap:16px; flex-wrap:wrap; }
.back-link { color:#1976D2; text-decoration:none; display:inline-flex; align-items:center; gap:8px; margin-top:8px; }
.muted { color:#666; margin-top:0; }
.frame-shell { background:#FFF; border:1px solid #EEE; border-radius:10px; box-shadow:0 2px 4px rgba(0,0,0,0.08); padding:12px; box-sizing:border-box; }
.settings-frame { width:100%; min-height:760px; border:0; border-radius:8px; background:#FFF; box-sizing:border-box; display:block; }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.back-link { color:#BB86FC; }
.muted{ color:#BBB; }
.frame-shell { border-color:#333; background:#1E1E1E; box-shadow:none; }
.settings-frame { background:#1E1E1E; }
}
"""


MODULE_CONFIGURE_FRAME_SCRIPT = r"""
(function() {
  var frame = document.getElementById('moduleSettingsFrame');
  function applyHeight(height) {
    if (!frame) return;
    var numeric = Number(height);
    if (!Number.isFinite(numeric) || numeric <= 0) return;
    frame.style.height = Math.max(360, Math.ceil(numeric) + 8) + 'px';
  }
  window.addEventListener('message', function(event) {
    if (event.origin !== window.location.origin) return;
    if (!event.data || event.data.type !== 'ops-frame-height') return;
    applyHeight(event.data.height);
  });
  if (frame) {
    frame.addEventListener('load', function() {
      try {
        applyHeight(frame.contentWindow.document.documentElement.scrollHeight);
      } catch (_error) {
      }
    });
  }
})();
"""


def module_safe_name(value):
    return re.fullmatch(r"[A-Za-z0-9_-]+", str(value or "")) is not None


def module_settings_frame_response(title, body, active="endpoints", user=None, status=200):
    return Response(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{h(title)}</title></head><body>{body}<script>
(function() {{
  function sendHeight() {{
    var body = document.body;
    var html = document.documentElement;
    var height = Math.max(
      body ? body.scrollHeight : 0,
      body ? body.offsetHeight : 0,
      html ? html.scrollHeight : 0,
      html ? html.offsetHeight : 0
    );
    if (window.parent && window.parent !== window) {{
      window.parent.postMessage({{ type: 'ops-frame-height', height: height }}, window.location.origin);
    }}
  }}
  window.addEventListener('load', sendHeight);
  window.addEventListener('resize', sendHeight);
  if (window.ResizeObserver) {{
    var observer = new ResizeObserver(sendHeight);
    observer.observe(document.documentElement);
    if (document.body) observer.observe(document.body);
  }} else {{
    setInterval(sendHeight, 300);
  }}
  setTimeout(sendHeight, 0);
}})();
</script></body></html>""",
        status=status,
        mimetype="text/html",
    )


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    module = request.args.get("module", "")
    info = endpoint_module_catalog().get(module)
    if not module_safe_name(module) or not info or not info.get("can_load", True) or not info.get("has_settings_page"):
        abort(404)
    mod = load_endpoint_web(module)
    if getattr(mod, "render_settings", None) is None:
        abort(404)
    if request.args.get("frame") == "1":
        return mod.render_settings(request, db, module_settings_frame_response, user)
    description = f'<p class="muted">{h(info.get("description") or "")}</p>' if info.get("description") else ""
    frame_src = "/admin/endpoint-module-settings-configure?" + urlencode({"module": module, "frame": "1"})
    content = f"""    <div class="header-actions">
        <div>
            <h1>{h(info.get("name") or module)} Settings</h1>
            {description}
        </div>
        <a class="back-link" href="/admin/endpoint-module-settings"><i class="fa-solid fa-arrow-left"></i> Manage Endpoint Modules</a>
    </div>
    <div class="frame-shell">
        <iframe id="moduleSettingsFrame" class="settings-frame" sandbox="allow-forms allow-same-origin allow-scripts allow-top-navigation" src="{h(frame_src)}" title="{h(info.get("name") or module)} settings"></iframe>
    </div>"""
    return legacy_page(f"{info.get('name') or module} Settings", legacy_user_context(user), "endpoints", MODULE_CONFIGURE_STYLE, content, MODULE_CONFIGURE_FRAME_SCRIPT)
