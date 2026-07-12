from srv.web.app import *
from group_features import fetch_group_rows

SEND_STYLE = r"""
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
#mobile-header{ display:none; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
@media(max-width:767px){ #mobile-header{ display:flex; } }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; }
.info-card{ background:#FFF; padding:16px; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; }
.info-row { display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid #f0f0f0; align-items: center; }
.info-row:last-child { border-bottom:none; }
.info-label { font-weight:500; color:#555; }
.md-checkbox-container { display:flex; align-items:center; position:relative; cursor:pointer; font-size:14px; font-weight:500; color:#555; user-select:none; width:100%; padding: 5px 0; }
.md-checkbox-container input { position:absolute; opacity:0; cursor:pointer; height:0; width:0; }
.md-checkmark { position:relative; display:inline-block; height:20px; width:20px; background-color:#fff; border:2px solid #5f6368; border-radius:2px; margin-right:12px; transition:all 0.2s; }
.md-checkbox-container:hover input ~ .md-checkmark { border-color:#202124; }
.md-checkbox-container input:checked ~ .md-checkmark { background-color:#1976D2; border-color:#1976D2; }
.md-checkmark:after { content:""; position:absolute; display:none; left:6px; top:2px; width:4px; height:10px; border:solid white; border-width:0 2px 2px 0; transform:rotate(45deg); }
.md-checkbox-container input:checked ~ .md-checkmark:after { display:block; }
.md-checkbox-container input:disabled ~ .md-checkmark { border-color:#dadce0; background-color:#f1f3f4; cursor:not-allowed; }
.md-checkbox-container input:disabled ~ .text { color:#9aa0a6; cursor:not-allowed; }
.recipient-note { color:#777; font-size:0.9em; margin-left:auto; white-space:nowrap; }
.recipient-row.unavailable { opacity:0.58; }
.recipient-row.unavailable .md-checkbox-container { cursor:not-allowed; }
.recipient-row.unavailable .md-checkmark { border-color:#BDBDBD; background:#F5F5F5; }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.info-card{ border:1px solid #333; background-color:#1E1E1E; }
.info-row { border-bottom:1px solid #333; }
.md-checkbox-container { color:#BBB; }
.md-checkmark { border-color:#9AA0A6; background-color:#1E1E1E; }
.md-checkbox-container:hover input ~ .md-checkmark { border-color:#E8EAED; }
.md-checkbox-container input:checked ~ .md-checkmark { background-color:#8AB4F8; border-color:#8AB4F8; }
.md-checkmark:after { border-color:#1E1E1E; }
.md-checkbox-container input:disabled ~ .md-checkmark { border-color:#5F6368; background-color:#3C4043; }
.md-checkbox-container input:disabled ~ .text { color:#5F6368; }
.recipient-note { color:#9E9E9E; }
.recipient-row.unavailable .md-checkmark { border-color:#555; background:#2A2A2A; }
}
.header-actions { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.btn-send { background:#2E7D32; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; transition: all 0.2s ease; box-shadow: 0 1px 2px rgba(0,0,0,0.1); }
.btn-send:hover { background:#1B5E20; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
.btn-cancel { background:#757575; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; transition: all 0.2s ease; box-shadow: 0 1px 2px rgba(0,0,0,0.1); }
.btn-cancel:hover { background:#616161; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
@media(prefers-color-scheme:dark){
    .btn-send { background:#81C784; color:#000; }
    .btn-send:hover { background:#66BB6A; }
    .btn-cancel { background:#B0BEC5; color:#000; }
    .btn-cancel:hover { background:#90A4AE; }
}
"""

SEND_SCRIPT = r"""
const sendAll = document.getElementById('send_all');
if (sendAll) {
    sendAll.addEventListener('change', function() {
        var isChecked = this.checked;
        var checkboxes = document.querySelectorAll('.group-checkbox');
        checkboxes.forEach(function(checkbox) {
            checkbox.disabled = isChecked || checkbox.dataset.unavailable === '1';
            if (isChecked) checkbox.checked = false;
        });
    });
}
"""


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    if not can_send_messages(user):
        abort(403)
    ctx = legacy_user_context(user)
    msgid = request.values.get("msgid", "")
    msg = query_one("SELECT name FROM messages WHERE messageid=%s LIMIT 1", (msgid,))
    if not msg:
        return redirect("/messages/")
    if not user_can_access_message(user, msgid):
        abort(403)
    conn = db()
    try:
        with conn.cursor() as cur:
            groups = fetch_group_rows(cur)
    finally:
        conn.close()
    groups = filter_group_rows_for_user(user, groups)
    allowed_group_ids = {str(group.get("id") or "").strip() for group in groups if str(group.get("id") or "").strip()}
    if request.method == "POST":
        if request.form.get("send_all"):
            targets = all_group_ids_value(user)
        else:
            selected_groups = [str(group_id or "").strip() for group_id in request.form.getlist("groups[]") if str(group_id or "").strip() in allowed_group_ids]
            if not selected_groups:
                return redirect(f"/messages/send?msgid={h(msgid)}")
            targets = ".".join(selected_groups)
        if not targets:
            return redirect(f"/messages/send?msgid={h(msgid)}")
        try:
            broadcast_id = create_broadcast(msgid, targets, user.get("username") or session.get("username") or "User")
            return redirect(f"/messages/send-status?bid={broadcast_id}")
        except Exception:
            fail_key = uuid.uuid4().hex
            try:
                from active_broadcast_store import RUNTIME_DIR
                RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
                with open(RUNTIME_DIR / f"send-debug-{fail_key}.log", "a", encoding="utf-8") as handle:
                    handle.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] send failed msgid={msgid} targets={targets}\n")
                    handle.write(traceback.format_exc() + "\n")
            except OSError:
                pass
            return redirect(f"/messages/send-status?fail={fail_key}")

    endpoint_data = endpoint_ipc("LIST_ENDPOINTS")
    endpoint_error = None if endpoint_data.get("ok", True) else endpoint_data.get("error") or "Endpoint manager returned an error."
    endpoint_availability = endpoint_availability_map(endpoint_data)
    all_unavailable = endpoint_error is None and not any_recipient_available(endpoint_availability)
    all_disabled = " disabled" if all_unavailable else ""
    all_row_cls = " recipient-row unavailable" if all_unavailable else ""
    all_note = '<span class="recipient-note">No available recipients</span>' if all_unavailable else ""
    if groups:
        group_rows = []
        for group in groups:
            recipients = list(group_member_tokens(group.get("members")))
            if "messages" in set(group.get("monitor_categories") or []):
                for member in group_member_tokens(group.get("monitor_members")):
                    if member not in recipients:
                        recipients.append(member)
            available = sum(1 for member in recipients if group_member_available(member, endpoint_availability))
            has_available = endpoint_error is not None or available > 0
            row_cls = "" if has_available else " unavailable"
            disabled = "" if has_available else " disabled"
            unavailable_data = "0" if has_available else "1"
            note = "" if has_available else '<span class="recipient-note">No available recipients</span>'
            group_rows.append(
                f"""                    <div class="info-row recipient-row{row_cls}">
                        <label class="md-checkbox-container">
                            <input type="checkbox" name="groups[]" value="{h(group.get("id"))}" class="group-checkbox" data-unavailable="{unavailable_data}"{disabled}>
                            <span class="md-checkmark"></span>
                            <span class="text">{h(group.get("name"))}</span>
                            {note}
                        </label>
                    </div>"""
            )
        group_html = "\n".join(group_rows)
    else:
        group_html = '                <div class="info-row"><span class="info-label" style="color:#777;">No groups available.</span></div>'
    error_html = ""
    content = f"""    <div class="header-actions">
        <h1>Sending {h(msg.get("name"))}</h1>
    </div>
    {error_html}

    <form action="/messages/send?msgid={h(msgid)}" method="POST" id="sendForm">
        <input type="hidden" name="msgid" value="{h(msgid)}">
        <div class="info-card">
            <div class="info-row{all_row_cls}">
                <label class="md-checkbox-container">
                    <input type="checkbox" name="send_all" id="send_all" value="1"{all_disabled}>
                    <span class="md-checkmark"></span>
                    <span class="text" style="font-weight: bold; color: #1976D2;">All Recipients</span>
                    {all_note}
                </label>
            </div>

{group_html}

            <div class="info-row" style="margin-top: 20px; justify-content: flex-end; gap: 15px; border-bottom: none;">
                <a href="/messages/" class="btn-cancel">Cancel</a>
                <button type="submit" class="btn-send"><i class="fa-solid fa-paper-plane" style="margin-right:8px;"></i> Send Message</button>
            </div>
        </div>
    </form>"""
    return legacy_page(f"Sending {msg.get('name')}", ctx, "messages", SEND_STYLE, content, SEND_SCRIPT)
