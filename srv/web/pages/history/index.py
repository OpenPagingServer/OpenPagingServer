from srv.web.app import *

HISTORY_STYLE = r"""
body, html { margin:0; padding:0; font-family:"Tahoma",sans-serif; font-weight:300; background-color:#FFF; height:100%; }
#sidebar { width:220px; background-color:#1976D2; color:#FFF; height:100vh; position:fixed; top:0; left:0; display:flex; flex-direction:column; box-shadow:2px 0 8px rgba(0,0,0,0.2); transition:transform 0.3s ease; z-index:1200; }
@media (max-width:767px){ #sidebar{ transform:translateX(-100%); } #sidebar.open{ transform:translateX(0); } }
#sidebar h2 { text-align:center; padding:20px 0; margin:0; font-weight:500; background-color:#1565C0; font-size:1.2em; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#FFF; padding:12px 20px; display:block; border-bottom:1px solid rgba(255,255,255,0.1); text-decoration:none; transition:background 0.3s; font-size:0.9em; text-align:left; box-sizing:border-box; }
#sidebar a i,.logout-btn i,.logout-btn-mobile i,.admin-only i { margin-right:8px; width:20px; text-align:center; }
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
.info-card{ background:#FFF; padding:16px; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; }
.info-row { display:flex; justify-content:space-between; padding:12px 0; border-bottom:1px solid #f0f0f0; align-items: center; }
.info-row:last-child { border-bottom:none; }
.info-label { font-weight:500; color:#333; }
.history-icon { color:#1976D2; font-size: 1.1em; width: 24px; text-align: center; }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.info-card{ border:1px solid #333; background-color:#1E1E1E; }
.info-label { color:#DDD; }
.info-row { border-bottom:1px solid #333; }
.history-icon { color:#BB86FC; }
}
.header-actions { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.msg-type { font-size: 0.85em; color: #777; font-weight: 400; white-space: nowrap; }
@media(prefers-color-scheme:dark){
    .msg-type { color: #AAA; }
}
"""


def _format_history_time(value):
    if isinstance(value, datetime):
        return f"{value.strftime('%b')} {value.day}, {value.year} {value:%H:%M:%S}"
    text = str(value or "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text[:19], fmt)
            return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year} {parsed:%H:%M:%S}"
        except ValueError:
            pass
    return text


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    rows = query_all("SELECT timestamp, message, icon FROM history ORDER BY timestamp DESC")
    if rows:
        row_html = []
        for row in rows:
            icon = h(row.get("icon")) if row.get("icon") else "fa-solid fa-circle-info"
            row_html.append(
                f"""                <div class="info-row">
                    <div style="display:flex; align-items:center; gap:12px;">
                        <i class="{icon} history-icon"></i>
                        <span class="info-label">{h(row.get("message"))}</span>
                    </div>
                    <div>
                        <span class="msg-type">{h(_format_history_time(row.get("timestamp")))}</span>
                    </div>
                </div>"""
            )
        items = "\n".join(row_html)
    else:
        items = '<p style="text-align:center; color:#777; padding: 20px;">No history records found.</p>'
    content = f"""    <div class="header-actions">
        <h1>History</h1>
    </div>

    <div class="info-card">
{items}
    </div>"""
    return legacy_page("History", legacy_user_context(user), "history", HISTORY_STYLE, content)
