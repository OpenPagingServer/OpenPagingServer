from srv.web.app import *
from broadcasts import (
    expand_broadcast_record_variables,
    expire_any_message_rule_broadcasts,
    expire_message_rule_broadcasts,
    parse_expires,
    put_active_broadcast,
    runtime_type,
)
from group_features import build_monitor_message_child_records
from group_features import fetch_group_rows
from srv.web.pages.messages.form_common import (
    MESSAGE_FORM_SCRIPT,
    MESSAGE_FORM_STYLE,
    audio_transfer_html,
    message_expiration_field_html,
    message_expiration_from_form,
    message_icon_field_html,
    message_variable_field_html,
    message_variable_guide_html,
    message_multiline_text,
    resolve_message_icon_value,
    vendor_specific_editor_html,
    vendor_specific_from_form,
)

CUSTOM_EXTRA_STYLE = r"""
.md-checkbox-container input:disabled ~ .text{color:#9aa0a6;cursor:not-allowed;}
.recipient-note{color:#777;font-size:0.9em;margin-left:auto;white-space:nowrap;}
.recipient-row.unavailable{opacity:0.58;}
.recipient-row.unavailable .md-checkbox-container{cursor:not-allowed;}
.recipient-row.unavailable .md-checkmark{border-color:#BDBDBD;background:#F5F5F5;}
.btn-send{background:#2E7D32;color:#FFF;border:none;padding:10px 16px;border-radius:6px;font-size:14px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;}
.btn-send:hover{background:#1B5E20;}
.btn-cancel{color:#777;text-decoration:none;}
.custom-form-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px;}
@media(prefers-color-scheme:dark){
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


def custom_form_state():
    return {
        "shortmessage": request.form.get("shortmessage", ""),
        "longmessage": message_multiline_text(request.form.get("longmessage", "")),
        "audio": [v.strip() for v in request.form.getlist("audio_files[]") if v.strip()],
        "groups": [str(group_id or "").strip() for group_id in request.form.getlist("groups[]") if str(group_id or "").strip()],
        "send_all": bool(request.form.get("send_all")),
        "color": str(request.form.get("color", "") or "").strip().lstrip("#").upper(),
        "icon": request.form.get("icon", ""),
        "priority": request.form.get("priority", "Normal"),
    }


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
        "vendor_specific": values.get("vendor_specific") or "",
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
            expand_broadcast_record_variables(cur, record, source_values=values)
            excluded_targets, monitor_children = build_monitor_message_child_records(cur, record)
            if excluded_targets:
                record["exclude_targets"] = list(excluded_targets)
            columns = table_columns("broadcasts")
            insert_columns = [column for column in record if column in columns]
            cur.execute(
                f"INSERT INTO broadcasts ({', '.join('`'+column+'`' for column in insert_columns)}) VALUES ({', '.join(['%s'] * len(insert_columns))})",
                tuple(record[column] for column in insert_columns),
            )
            put_active_broadcast(record)
            for child in monitor_children:
                put_active_broadcast(child)
            trigger_priority = record.get("priority")
            if str(trigger_priority or "").strip().lower() != "emergency":
                expire_message_rule_broadcasts(
                    cur,
                    normalized_rule,
                    [broadcast_id],
                    trigger_groups=record.get("groups"),
                )
                expire_any_message_rule_broadcasts(
                    cur,
                    [broadcast_id],
                    trigger_groups=record.get("groups"),
                )
        conn.commit()
    finally:
        conn.close()
    return broadcast_id

def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    if not can_send_messages(user):
        abort(403)
    ctx = legacy_user_context(user)
    if demo_mode_enabled():
        return demo_mode_iframe_html("messages")
    ensure_message_vendor_schema()
    error = ""
    form_state = custom_form_state()

    if request.method == "POST":
        audio_value = ":".join(form_state["audio"])
        shortmessage = form_state["shortmessage"]
        longmessage = form_state["longmessage"]
        has_audio = bool(audio_value)
        has_text = bool(shortmessage.strip() or longmessage.strip())
        if not has_audio and not has_text:
            error = "Enter a message, add audio, or both."
        msg_type = "text+audio" if (has_audio and has_text) else ("audio" if has_audio else "text")
        if not error and form_state["send_all"]:
            groups_value = all_group_ids_value(user)
        elif not error:
            allowed_group_ids = {
                str(group.get("id") or "").strip()
                for group in filter_group_rows_for_user(
                    user,
                    query_all("SELECT id FROM `groups` ORDER BY name ASC"),
                )
                if str(group.get("id") or "").strip()
            }
            selected = [group_id for group_id in form_state["groups"] if group_id in allowed_group_ids]
            groups_value = ".".join(selected)
        else:
            groups_value = ""
        if not error and groups_value in {"", "0"}:
            error = "Select at least one group, or choose All Recipients."
        if not error:
            expires_rule = message_expiration_from_form(request.form)
            try:
                broadcast_id = create_custom_broadcast(
                    {
                        "name": "Custom message",
                        "shortmessage": shortmessage if has_text else "",
                        "longmessage": longmessage if has_text else "",
                        "icon": resolve_message_icon_value(form_state["icon"]) if has_text else "",
                        "color": form_state["color"] if has_text else "",
                        "groups": groups_value,
                        "image": request.form.get("image", ""),
                        "audio": audio_value,
                        "sender": user.get("username") or session.get("username") or "User",
                        "priority": form_state["priority"],
                        "type": msg_type,
                        "vendor_specific": vendor_specific_from_form(request.form),
                    },
                    expires_rule,
                )
                return redirect(f"/messages/send-status?bid={broadcast_id}")
            except Exception:
                fail_key = uuid.uuid4().hex
                try:
                    from active_broadcast_store import RUNTIME_DIR
                    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
                    with open(RUNTIME_DIR / f"send-debug-{fail_key}.log", "a", encoding="utf-8") as handle:
                        handle.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] custom send failed groups={groups_value}\n")
                        handle.write(traceback.format_exc() + "\n")
                except OSError:
                    pass
                return redirect(f"/messages/send-status?fail={fail_key}")

    conn = db()
    try:
        with conn.cursor() as cur:
            groups = fetch_group_rows(cur)
    finally:
        conn.close()
    groups = filter_group_rows_for_user(user, groups)
    expiration_messages = filter_message_rows_for_user(
        user,
        query_all("SELECT messageid, name FROM messages ORDER BY name ASC, messageid ASC"),
    )
    endpoint_data = endpoint_ipc("LIST_ENDPOINTS")
    endpoint_error = None if endpoint_data.get("ok", True) else endpoint_data.get("error") or "Endpoint manager returned an error."
    endpoint_availability = endpoint_availability_map(endpoint_data)
    all_unavailable = endpoint_error is None and not any_recipient_available(endpoint_availability)
    all_disabled = " disabled" if all_unavailable else ""
    all_row_cls = " recipient-row unavailable" if all_unavailable else ""
    all_note = '<span class="recipient-note">No available recipients</span>' if all_unavailable else ""
    selected_groups = set(form_state["groups"])
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
                f"""                    <label class="md-checkbox-container recipient-row{row_cls}">
                        <input type="checkbox" name="groups[]" value="{h(group.get("id"))}" class="group-checkbox" data-unavailable="{unavailable_data}"{disabled}{' checked' if str(group.get("id") or '').strip() in selected_groups else ''}>
                        <span class="md-checkmark"></span>
                        <span class="text">{h(group.get("name"))}</span>
                        {note}
                    </label>"""
            )
        groups_html = "\n".join(group_rows)
    else:
        groups_html = '<p class="help-text">No groups are available.</p>'
    transfer = audio_transfer_html(audio_files(), form_state["audio"])
    vendor_specific_html = vendor_specific_editor_html(context={"mode": "message_custom"})
    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    variable_fields = (
        message_variable_field_html(
            "shortmessage",
            "Short Message",
            f'<input type="text" name="shortmessage" id="shortmessage" class="form-control" value="{h(form_state["shortmessage"])}">',
            "You can use variables here and they will resolve when the custom message is sent.",
        )
        + message_variable_field_html(
            "longmessage",
            "Long Message",
            f'<textarea name="longmessage" id="longmessage" class="form-control textarea-long" rows="7" wrap="soft">{h(form_state["longmessage"])}</textarea>',
            "Use variables for timestamps, sender details, live API text, or the product name.",
        )
    )
    content = f"""    <div class="header-actions">
        <h1>Send Custom Message</h1>
    </div>
    <div class="info-card">
        {error_html}
        <form method="POST">
            <div class="form-group">
                <label class="main-label">Recipients</label>
                <div class="checkbox-row">
                    <label class="md-checkbox-container">
                        <input type="checkbox" name="send_all" id="send_all" value="1" onchange="toggleRecipients()"{all_disabled}{' checked' if form_state["send_all"] else ''}>
                        <span class="md-checkmark"></span>
                        <span class="text" style="font-weight:bold;color:#1976D2;">All Recipients</span>
                        {all_note}
                    </label>
{groups_html}
                </div>
            </div>
            <div id="audio-fields" class="form-group">
                <label class="main-label">Audio</label>
                <p class="help-text">Optional. If you only add audio, an audio message is sent. If you only enter text, a visual message is sent. Adding both sends an audio &amp; visual message.</p>
                {transfer}
            </div>
            <div id="visual-fields">
{variable_fields}
{message_icon_field_html(form_state["icon"])}
                <div class="form-group">
                    <label class="main-label">Color</label>
                <div class="color-picker-container">
                    <input type="color" id="colorPicker" value="{h('#' + form_state["color"] if re.fullmatch(r'[A-F0-9]{6}', form_state['color']) else '#000000')}" class="color-picker-input">
                    <input type="text" name="color" id="colorHex" class="form-control" style="width:150px;" placeholder="000000" maxlength="6" value="{h(form_state["color"])}">
                </div>
            </div>
            </div>
            {message_expiration_field_html(expiration_messages)}
            <div class="form-group">
                <label class="main-label" for="priority">Priority</label>
                <select name="priority" id="priority" class="form-control">
                    <option value="Low"{' selected' if form_state["priority"] == 'Low' else ''}>Low</option>
                    <option value="Normal"{' selected' if form_state["priority"] == 'Normal' else ''}>Normal</option>
                    <option value="High"{' selected' if form_state["priority"] == 'High' else ''}>High</option>
                    <option value="Emergency"{' selected' if form_state["priority"] == 'Emergency' else ''}>Emergency</option>
                </select>
            </div>
            {vendor_specific_html}
            <div class="custom-form-actions">
                <button type="submit" class="btn-send"><i class="fa-solid fa-paper-plane" style="margin-right:8px;"></i> Send Custom Message</button>
                <a href="/messages/" class="btn-cancel">Cancel</a>
            </div>
        </form>
    </div>
{message_variable_guide_html()}"""
    return legacy_page("Send Custom Message", ctx, "messages", MESSAGE_FORM_STYLE + CUSTOM_EXTRA_STYLE, content, CUSTOM_SCRIPT)
