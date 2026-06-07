from srv.web.app import *
from broadcasts import expire_message_rule_broadcasts, parse_expires, put_active_broadcast, runtime_type
from srv.web.pages.messages.form_common import (
    MESSAGE_FORM_SCRIPT,
    MESSAGE_FORM_STYLE,
    audio_transfer_html,
    message_multiline_text,
)

CUSTOM_EXTRA_STYLE = r"""
.md-checkbox-container{display:flex;align-items:center;position:relative;cursor:pointer;font-size:14px;font-weight:500;color:#555;user-select:none;width:100%;padding:5px 0;}
.md-checkbox-container input{position:absolute;opacity:0;cursor:pointer;height:0;width:0;}
.md-checkmark{position:relative;display:inline-block;height:20px;width:20px;background:#fff;border:2px solid #5f6368;border-radius:2px;margin-right:12px;transition:all 0.2s;flex:0 0 auto;}
.md-checkbox-container:hover input ~ .md-checkmark{border-color:#202124;}
.md-checkbox-container input:checked ~ .md-checkmark{background:#1976D2;border-color:#1976D2;}
.md-checkmark:after{content:"";position:absolute;display:none;left:6px;top:2px;width:4px;height:10px;border:solid white;border-width:0 2px 2px 0;transform:rotate(45deg);}
.md-checkbox-container input:checked ~ .md-checkmark:after{display:block;}
.md-checkbox-container input:disabled ~ .md-checkmark{border-color:#dadce0;background:#f1f3f4;cursor:not-allowed;}
.md-checkbox-container input:disabled ~ .text{color:#9aa0a6;cursor:not-allowed;}
.recipient-note{color:#777;font-size:0.9em;margin-left:auto;white-space:nowrap;}
.recipient-row.unavailable{opacity:0.58;}
.recipient-row.unavailable .md-checkbox-container{cursor:not-allowed;}
.recipient-row.unavailable .md-checkmark{border-color:#BDBDBD;background:#F5F5F5;}
.btn-send{background:#2E7D32;color:#FFF;border:none;padding:10px 16px;border-radius:6px;font-size:14px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;}
.btn-send:hover{background:#1B5E20;}
.btn-cancel{color:#777;text-decoration:none;margin-left:10px;}
@media(prefers-color-scheme:dark){
    .md-checkbox-container{color:#BBB;}
    .md-checkmark{border-color:#9AA0A6;background:#1E1E1E;}
    .md-checkbox-container:hover input ~ .md-checkmark{border-color:#E8EAED;}
    .md-checkbox-container input:checked ~ .md-checkmark{background:#8AB4F8;border-color:#8AB4F8;}
    .md-checkmark:after{border-color:#1E1E1E;}
    .md-checkbox-container input:disabled ~ .md-checkmark{border-color:#5F6368;background:#3C4043;}
    .recipient-note{color:#9E9E9E;}
    .recipient-row.unavailable .md-checkmark{border-color:#555;background:#2A2A2A;}
    .btn-send{background:#81C784;color:#000;}
    .btn-send:hover{background:#66BB6A;}
}
"""

CUSTOM_SCRIPT = MESSAGE_FORM_SCRIPT + r"""
function toggleRecipients(){
    const all = document.getElementById('send_all').checked;
    document.querySelectorAll('.group-checkbox').forEach(cb => {
        cb.disabled = all || cb.dataset.unavailable === '1';
        if (all) cb.checked = false;
    });
}
"""


def create_custom_broadcast(values, expires_rule):
    issued = datetime.now()
    expires_at, normalized_rule = parse_expires(expires_rule, issued)
    broadcast_id = uuid.uuid4().hex
    record = {
        "id": broadcast_id,
        "name": values.get("name") or "Custom message",
        "shortmessage": values.get("shortmessage") or "",
        "longmessage": values.get("longmessage") or "",
        "icon": values.get("icon") or "",
        "color": values.get("color") or "",
        "vendor_specific": "",
        "expires_rule": normalized_rule,
        "type": runtime_type(values.get("type")),
        "expires": expires_at,
        "issued": issued,
        "groups": values.get("groups") or "",
        "image": values.get("image") or "",
        "audio": values.get("audio") or "",
        "sender": values.get("sender") or "",
        "priority": values.get("priority") or "Normal",
        "delivery": "pending",
    }
    conn = db()
    try:
        with conn.cursor() as cur:
            columns = table_columns("broadcasts")
            insert_columns = [column for column in record if column in columns]
            cur.execute(
                f"INSERT INTO broadcasts ({', '.join('`'+column+'`' for column in insert_columns)}) VALUES ({', '.join(['%s'] * len(insert_columns))})",
                tuple(record[column] for column in insert_columns),
            )
            put_active_broadcast(record)
            expire_message_rule_broadcasts(cur, normalized_rule, [broadcast_id])
        conn.commit()
    finally:
        conn.close()
    return broadcast_id

def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ctx = legacy_user_context(user)
    if demo_mode_enabled():
        return demo_mode_iframe_html("messages")

    if request.method == "POST":
        if request.form.get("send_all"):
            groups_value = "0"
        else:
            selected = [str(int(group_id)) for group_id in request.form.getlist("groups[]")]
            groups_value = ".".join(selected)
        if groups_value == "":
            return redirect("/messages/custom")
        expires_rule = request.form.get("expires", "manual").strip() or "manual"
        create_custom_broadcast(
            {
                "name": "Custom message",
                "shortmessage": request.form.get("shortmessage", ""),
                "longmessage": message_multiline_text(request.form.get("longmessage", "")),
                "icon": request.form.get("icon", ""),
                "color": request.form.get("color", "").lstrip("#"),
                "groups": groups_value,
                "image": request.form.get("image", ""),
                "audio": ":".join(request.form.getlist("audio_files[]")),
                "sender": user.get("username") or session.get("username") or "User",
                "priority": request.form.get("priority", "Normal"),
                "type": request.form.get("type", "text"),
            },
            expires_rule,
        )
        return redirect("/messages/")

    groups = query_all("SELECT id, name, members FROM `groups` ORDER BY name ASC")
    endpoint_data = endpoint_ipc("LIST_ENDPOINTS")
    endpoint_error = None if endpoint_data.get("ok", True) else endpoint_data.get("error") or "Endpoint manager returned an error."
    endpoint_availability = endpoint_availability_map(endpoint_data)
    total_available = sum(
        1
        for group in groups
        for member in group_member_tokens(group.get("members"))
        if group_member_available(member, endpoint_availability)
    )
    all_unavailable = endpoint_error is None and total_available == 0
    all_disabled = " disabled" if all_unavailable else ""
    all_row_cls = " recipient-row unavailable" if all_unavailable else ""
    all_note = '<span class="recipient-note">No available recipients</span>' if all_unavailable else ""
    if groups:
        group_rows = []
        for group in groups:
            available = sum(1 for member in group_member_tokens(group.get("members")) if group_member_available(member, endpoint_availability))
            has_available = endpoint_error is not None or available > 0
            row_cls = "" if has_available else " unavailable"
            disabled = "" if has_available else " disabled"
            unavailable_data = "0" if has_available else "1"
            note = "" if has_available else '<span class="recipient-note">No available recipients</span>'
            group_rows.append(
                f"""                    <label class="md-checkbox-container recipient-row{row_cls}">
                        <input type="checkbox" name="groups[]" value="{h(group.get("id"))}" class="group-checkbox" data-unavailable="{unavailable_data}"{disabled}>
                        <span class="md-checkmark"></span>
                        <span class="text">{h(group.get("name"))}</span>
                        {note}
                    </label>"""
            )
        groups_html = "\n".join(group_rows)
    else:
        groups_html = '<p class="help-text">No groups are available.</p>'
    transfer = audio_transfer_html(audio_files())
    content = f"""    <div class="header-actions">
        <h1>Send Custom Message</h1>
    </div>
    <div class="info-card">
        <form method="POST">
            <div class="form-group">
                <label class="main-label">Recipients</label>
                <div class="checkbox-row">
                    <label class="md-checkbox-container">
                        <input type="checkbox" name="send_all" id="send_all" value="1" onchange="toggleRecipients()"{all_disabled}>
                        <span class="md-checkmark"></span>
                        <span class="text" style="font-weight:bold;color:#1976D2;">All Recipients</span>
                        {all_note}
                    </label>
{groups_html}
                </div>
            </div>
            <div class="form-group">
                <label class="main-label">Message Type</label>
                <div class="radio-group">
                    <label><input type="radio" name="type" value="text+audio" onchange="toggleFields()" required> Audio & visual message</label>
                    <label><input type="radio" name="type" value="audio" onchange="toggleFields()"> Audio message</label>
                    <label><input type="radio" name="type" value="text" onchange="toggleFields()"> Visual message</label>
                </div>
            </div>
            <div id="visual-fields" style="display:none;">
                <div class="form-group">
                    <label class="main-label" for="shortmessage">Short Message</label>
                    <input type="text" name="shortmessage" id="shortmessage" class="form-control">
                </div>
                <div class="form-group">
                    <label class="main-label" for="longmessage">Long Message</label>
                    <textarea name="longmessage" id="longmessage" class="form-control textarea-long" rows="7" wrap="soft"></textarea>
                </div>
                <div class="form-group">
                    <label class="main-label">Color</label>
                    <div class="color-picker-container">
                        <input type="color" id="colorPicker" value="#000000" class="color-picker-input">
                        <input type="text" name="color" id="colorHex" class="form-control" style="width:150px;" placeholder="000000" maxlength="6">
                    </div>
                </div>
            </div>
            <div id="audio-fields" style="display:none;" class="form-group">
                <label class="main-label">Audio</label>
                {transfer}
            </div>
            <div class="form-group">
                <label class="main-label" for="expires">Expiration</label>
                <p class="help-text">Use 30m or 15m, msg=3 or msg=3.4, or manual for no automatic expiration.</p>
                <input type="text" name="expires" id="expires" class="form-control" value="manual">
            </div>
            <div class="form-group">
                <label class="main-label" for="priority">Priority</label>
                <select name="priority" id="priority" class="form-control">
                    <option value="Low">Low</option>
                    <option value="Normal" selected>Normal</option>
                    <option value="High">High</option>
                    <option value="Emergency">Emergency</option>
                </select>
            </div>
            <button type="submit" class="btn-send"><i class="fa-solid fa-paper-plane" style="margin-right:8px;"></i> Send Custom Message</button>
            <a href="/messages/" class="btn-cancel">Cancel</a>
        </form>
    </div>"""
    return legacy_page("Send Custom Message", ctx, "messages", MESSAGE_FORM_STYLE + CUSTOM_EXTRA_STYLE, content, CUSTOM_SCRIPT)
