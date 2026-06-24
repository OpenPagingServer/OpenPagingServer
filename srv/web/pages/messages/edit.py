from srv.web.app import *
from srv.web.pages.messages.form_common import (
    MESSAGE_FORM_SCRIPT,
    MESSAGE_FORM_STYLE,
    audio_transfer_html,
    message_expiration_field_html,
    message_expiration_from_form,
    message_variable_field_html,
    message_variable_guide_html,
    message_multiline_text,
    vendor_specific_editor_html,
    vendor_specific_from_form,
)

def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ctx = legacy_user_context(user)
    if not ctx["is_admin"]:
        abort(403)
    if demo_mode_enabled():
        return demo_mode_iframe_html("messages")
    ensure_message_vendor_schema()

    msgid = request.values.get("msgid", "")
    if not re.fullmatch(r"\d+", str(msgid or "")):
        abort(400)
    row = query_one("SELECT * FROM messages WHERE messageid=%s LIMIT 1", (msgid,))
    if not row:
        abort(404)
    columns = table_columns("messages")
    message_type = str(row.get("type") or "")
    show_visual = message_type in {"text", "text+audio"}
    show_audio = message_type in {"audio", "text+audio"}
    error = ""

    if request.method == "POST":
        try:
            updates = {}
            name = request.form.get("name", "").strip()
            if not name:
                raise RuntimeError("Name is required.")
            if "name" in columns:
                updates["name"] = name
            if show_visual:
                if "shortmessage" in columns:
                    updates["shortmessage"] = request.form.get("shortmessage", "")
                if "longmessage" in columns:
                    updates["longmessage"] = message_multiline_text(request.form.get("longmessage", ""))
                if "color" in columns:
                    color = request.form.get("color", "").strip().lstrip("#").upper()
                    if color and not re.fullmatch(r"[A-F0-9]{6}", color):
                        raise RuntimeError("Color must be a 6 character hex value.")
                    updates["color"] = color
            if show_audio and "audio" in columns:
                updates["audio"] = ":".join([v.strip() for v in request.form.getlist("audio_files[]") if v.strip()])
            if "expires" in columns:
                updates["expires"] = message_expiration_from_form(request.form)
            if "priority" in columns:
                updates["priority"] = request.form.get("priority", "Normal")
            if "vendor_specific" in columns:
                updates["vendor_specific"] = vendor_specific_from_form(request.form, row.get("vendor_specific"))
            if updates:
                execute(
                    f"UPDATE messages SET {', '.join('`'+k+'`=%s' for k in updates)} WHERE messageid=%s",
                    tuple(updates.values()) + (msgid,),
                )
            return redirect("/messages/")
        except Exception as exc:
            error = str(exc)
            row.update(request.form.to_dict())

    selected_audio = [item for item in str(row.get("audio") or "").split(":") if item.strip()]
    expiration_messages = query_all(
        "SELECT messageid, name FROM messages WHERE messageid <> %s ORDER BY name ASC, messageid ASC",
        (msgid,),
    )
    color_value = str(row.get("color") or "").strip().lstrip("#").upper()
    color_picker = "#" + color_value if re.fullmatch(r"[A-Fa-f0-9]{6}", color_value) else "#000000"
    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    visual_html = ""
    if show_visual:
        visual_html = f"""
            <div id="visual-fields">
{message_variable_field_html(
    "shortmessage",
    "Short Message",
    f'<input type="text" name="shortmessage" id="shortmessage" class="form-control" value="{h(row.get("shortmessage"))}">',
    "Enter the short text message. Usually shown on previews and on wall-mounted devices. This should be brief. You can use variables.",
)}
{message_variable_field_html(
    "longmessage",
    "Long Message",
    f'<textarea name="longmessage" id="longmessage" class="form-control textarea-long" rows="7" wrap="soft">{h(row.get("longmessage"))}</textarea>',
    'Enter the long text message. Usually shown on apps, and in a "more details" section. This should contain as much information as a user would need to know about the situation or incident associated with the message.',
)}
                <div class="form-group">
                    <label class="main-label">Color</label>
                    <p class="help-text">Certain endpoints can show a color-coded message.</p>
                    <div class="color-picker-container">
                        <input type="color" id="colorPicker" value="{h(color_picker)}" class="color-picker-input">
                        <input type="text" name="color" id="colorHex" class="form-control" style="width: 150px;" placeholder="000000" maxlength="6" value="{h(color_value)}">
                    </div>
                </div>
            </div>"""
    audio_html = ""
    if show_audio:
        audio_html = f"""
            <div id="audio-fields" class="form-group">
                <label class="main-label">Audio</label>
                <p class="help-text">Select audio files to include in this message. The files will play in the order listed in the selected column. You can click to select and use buttons, or drag and drop to move and reorder.</p>
                {audio_transfer_html(audio_files(), selected_audio)}
            </div>"""
    vendor_specific_html = vendor_specific_editor_html(
        row.get("vendor_specific") or "",
        message=row,
        context={"mode": "message_edit", "message_id": msgid},
    )
    content = f"""    <div class="header-actions">
        <h1>Edit Message</h1>
    </div>

    <div class="info-card">
        {error_html}
        <form method="POST">
            <input type="hidden" name="msgid" value="{h(row.get("messageid") or msgid)}">

            <div class="form-group">
                <label class="main-label" for="name">Name</label>
                <p class="help-text">Enter the name of the message. It will be shown in the interface, and may show up on certain endpoints.</p>
                <input type="text" name="name" id="name" class="form-control" value="{h(row.get("name"))}" required>
            </div>

            {visual_html}
            {audio_html}

            {message_expiration_field_html(expiration_messages, row.get("expires") or "manual")}

            <div class="form-group">
                <label class="main-label" for="priority">Priority</label>
                <select name="priority" id="priority" class="form-control">
                    <option value="Low"{" selected" if str(row.get("priority") or "Normal") == "Low" else ""}>Low</option>
                    <option value="Normal"{" selected" if str(row.get("priority") or "Normal") == "Normal" else ""}>Normal</option>
                    <option value="High"{" selected" if str(row.get("priority") or "Normal") == "High" else ""}>High</option>
                    <option value="Emergency"{" selected" if str(row.get("priority") or "Normal") == "Emergency" else ""}>Emergency</option>
                </select>
            </div>

            {vendor_specific_html}

            <div style="margin-top: 20px;">
                <button type="submit" class="btn-primary">Save Message</button>
                <a href="/messages/" style="margin-left:10px; color:#777; text-decoration:none;">Cancel</a>
            </div>
        </form>
    </div>
{message_variable_guide_html() if show_visual else ""}"""
    return legacy_page("Edit Message", ctx, "messages", MESSAGE_FORM_STYLE, content, MESSAGE_FORM_SCRIPT)
