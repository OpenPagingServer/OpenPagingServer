from srv.web.app import *

MESSAGES_STYLE = r"""
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
.info-card{ background:#FFF; padding:16px; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; }
.info-row { display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid #f0f0f0; align-items: center; }
.info-row:last-child { border-bottom:none; }
.info-label { font-weight:500; color:#555; }
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
.info-label { color:#BBB; }
.info-row { border-bottom:1px solid #333; }
}
.header-actions { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.btn-primary { background:#1976D2; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; }
.btn-primary:hover { background:#1565C0; }
.btn-custom-send { background:#2E7D32; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; }
.btn-custom-send:hover { background:#1B5E20; }
.btn-send { background:#2E7D32; color:#FFF; border:none; padding:8px 12px; border-radius:4px; font-size:13px; cursor:pointer; text-decoration:none; }
.btn-send:hover { background:#1B5E20; }
@media(prefers-color-scheme:dark){ .btn-primary { background:#BB86FC; color:#000; } .btn-primary:hover { background:#A370F7; } .btn-custom-send { background:#81C784; color:#000; } .btn-custom-send:hover { background:#66BB6A; } }
.msg-type { font-size: 0.8em; color: #777; font-weight: 400; display: block; }
.dropdown { position: relative; display: inline-block; }
.dropbtn { background: none; border: none; font-size: 1.2em; cursor: pointer; color: #777; padding: 5px 10px; }
.dropdown-content { display: none; position: absolute; right: 0; background-color: #f9f9f9; min-width: 120px; box-shadow: 0px 8px 166px 0px rgba(0,0,0,0.2); z-index: 1; border-radius: 4px; }
.dropdown-content a { color: black; padding: 12px 16px; text-decoration: none; display: block; font-size: 14px; }
.dropdown-content a:hover { background-color: #f1f1f1; }
@media(prefers-color-scheme:dark){
    .msg-type { color: #AAA; }
    .dropbtn { color: #BBB; }
    .dropdown-content { background-color: #333; }
    .dropdown-content a { color: #EEE; }
    .dropdown-content a:hover { background-color: #444; }
}
"""

MESSAGES_SCRIPT = r"""
function toggleDropdown(btn) {
    document.querySelectorAll('.dropdown-content.open').forEach(function(el) {
        if (!el.contains(btn)) el.classList.remove('open');
    });
    var menu = btn.nextElementSibling;
    if (menu) menu.classList.toggle('open');
}
document.addEventListener('click', function(event) {
    document.querySelectorAll('.dropdown-content.open').forEach(function(el) {
        if (!el.contains(event.target) && !el.previousElementSibling.contains(event.target)) {
            el.classList.remove('open');
        }
    });
});
"""


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ctx = legacy_user_context(user)
    demo = demo_mode_enabled()
    if request.args.get("delete_msgid") and ctx["is_admin"]:
        if demo:
            return demo_mode_iframe_html("messages")
        execute("DELETE FROM messages WHERE messageid=%s", (request.args["delete_msgid"],))
        return redirect("/messages/")
    rows = query_all("SELECT messageid, name, type FROM messages ORDER BY name ASC")
    admin_new = ('<a href="javascript:openDemoModePopup(\'messages\')" class="btn-primary"><i class="fa-solid fa-plus" style="margin-right:8px;"></i> New Message</a>' if demo else '<a href="/messages/new" class="btn-primary"><i class="fa-solid fa-plus" style="margin-right:8px;"></i> New Message</a>') if ctx["is_admin"] else ""
    if rows:
        rendered = []
        for row in rows:
            mid = h(row.get("messageid"))
            admin_menu = ""
            if ctx["is_admin"]:
                edit_href = "javascript:openDemoModePopup('messages')" if demo else f"/messages/edit?msgid={mid}"
                delete_href = "javascript:openDemoModePopup('messages')" if demo else f"?delete_msgid={mid}"
                delete_click = "" if demo else " onclick=\"return confirm('Are you sure you want to delete this message?')\""
                admin_menu = f"""
                        <div class="dropdown">
                            <button class="dropbtn" onclick="event.stopPropagation(); toggleDropdown(this);"><i class="fa-solid fa-ellipsis-vertical"></i></button>
                            <div class="dropdown-content">
                                <a href="{edit_href}"><i class="fa-solid fa-pen-to-square"></i> Edit</a>
                                <a href="{delete_href}"{delete_click} style="color:#C62828;"><i class="fa-solid fa-trash"></i> Delete</a>
                            </div>
                        </div>"""
            rendered.append(
                f"""                <div class="info-row">
                    <div>
                        <span class="info-label">{h(row.get("name"))}</span>
                        <span class="msg-type">{h(row.get("type"))}</span>
                    </div>
                    <div style="display:flex; align-items:center; gap:10px;">
                        <a href="/messages/send?msgid={mid}" class="btn-send"><i class="fa-solid fa-paper-plane"></i> Send</a>
                        {admin_menu}
                    </div>
                </div>"""
            )
        message_items = "\n".join(rendered)
    else:
        message_items = '<p style="text-align:center; color:#777; padding: 20px;">No messages</p>'
    content = f"""    <div class="header-actions">
        <h1>Messages</h1>
        <div style="display:flex; gap:10px; align-items:center;">
        <a href="{"javascript:openDemoModePopup('messages')" if demo else "/messages/custom"}" class="btn-custom-send"><i class="fa-solid fa-paper-plane" style="margin-right:8px;"></i> Send Custom Message</a>
        {admin_new}
        </div>
    </div>

    <div class="info-card">
{message_items}
    </div>"""
    return legacy_page("Messages", ctx, "messages", MESSAGES_STYLE, content, MESSAGES_SCRIPT, "<style>\n.dropdown-content.open { display: block; }\n</style>")
