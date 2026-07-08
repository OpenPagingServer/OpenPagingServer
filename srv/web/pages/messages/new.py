from srv.web.app import *
from srv.web.pages.messages.form_common import (
    MESSAGE_FORM_SCRIPT,
    MESSAGE_FORM_STYLE,
    audio_transfer_html,
    message_icon_field_html,
    resolve_message_icon_value,
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
    if not can_create_messages(user):
        abort(403)
    if demo_mode_enabled():
        return demo_mode_iframe_html("messages")
    ensure_message_vendor_schema()

    error = ""
    if request.method == "POST":
        audio = ":".join([v.strip() for v in request.form.getlist("audio_files[]") if v.strip()])
        shortmessage = request.form.get("shortmessage", "")
        longmessage = message_multiline_text(request.form.get("longmessage", ""))
        has_audio = bool(audio)
        has_text = bool(shortmessage.strip() or longmessage.strip())
        if not has_audio and not has_text:
            error = "Enter a message, add audio, or both."
        else:
            msg_type = "text+audio" if (has_audio and has_text) else ("audio" if has_audio else "text")
            values = {
                "messageid": next_message_id(),
                "name": request.form.get("name", ""),
                "type": msg_type,
                "shortmessage": shortmessage if has_text else "",
                "longmessage": longmessage if has_text else "",
                "color": request.form.get("color", "").lstrip("#") if has_text else "",
                "icon": resolve_message_icon_value(request.form.get("icon", "")) if has_text else "",
                "audio": audio,
                "expires": message_expiration_from_form(request.form),
                "priority": request.form.get("priority", "Normal"),
                "vendor_specific": vendor_specific_from_form(request.form),
            }

            columns = table_columns("messages")
            wanted = ["messageid", "name", "type", "shortmessage", "longmessage", "color", "icon", "audio", "expires", "priority", "vendor_specific", "owner_user_id"]
            insert = {k: values.get(k, "") for k in wanted if k in columns}
            if "owner_user_id" in insert:
                insert["owner_user_id"] = user.get("id")

            execute(
                f"INSERT INTO messages ({', '.join('`'+k+'`' for k in insert)}) VALUES ({', '.join(['%s'] * len(insert))})",
                tuple(insert.values()),
            )

            return redirect("/messages/")

    expiration_messages = filter_message_rows_for_user(
        user,
        query_all("SELECT messageid, name FROM messages ORDER BY name ASC, messageid ASC"),
    )

    error_html = f'<div class="error">{h(error)}</div>' if error else ""
    audio_fields = f"""            <div class="form-group">
                <label class="main-label">Audio</label>
                <p class="help-text">Optional. If you only add audio, this is saved as an audio message. If you only enter text, it is saved as a visual message. Adding both saves an audio &amp; visual message.</p>
                {audio_transfer_html(audio_files())}
            </div>
"""
    visual_fields = (
        message_variable_field_html(
            "shortmessage",
            "Short Message",
            f'<input type="text" name="shortmessage" id="shortmessage" class="form-control" value="{h(request.form.get("shortmessage", ""))}">',
            "Enter the short text message. Usually shown on previews and on wall-mounted devices. This should be brief. You can use variables.",
        )
        + message_variable_field_html(
            "longmessage",
            "Long Message",
            f'<textarea name="longmessage" id="longmessage" class="form-control textarea-long" rows="7" wrap="soft">{h(request.form.get("longmessage", ""))}</textarea>',
            'Enter the long text message. Usually shown on apps, and in a "more details" section. This should contain as much information as a user would need to know about the situation or incident associated with the message.',
        )
        + message_icon_field_html()
        + """            <div class="form-group">
                <label class="main-label">Color</label>
                <p class="help-text">Certain endpoints can show a color-coded message.</p>
                <div class="color-picker-container">
                    <input type="color" id="colorPicker" value="#000000" class="color-picker-input">
                    <input type="text" name="color" id="colorHex" class="form-control" style="width: 150px;" placeholder="000000" maxlength="6">
                </div>
            </div>
"""
    )

    vendor_specific_html = vendor_specific_editor_html(context={"mode": "message_new", "message_type": "text+audio"})
    content = f"""    <div class="header-actions">
        <h1>New Message</h1>
    </div>

    <div class="info-card">
        {error_html}
        <form method="POST">
            <div class="form-group">
                <label class="main-label" for="name">Name</label>
                <p class="help-text">Enter the name of the message. It will be shown in the interface, and may show up on certain endpoints.</p>
                <input type="text" name="name" id="name" class="form-control" value="{h(request.form.get("name", ""))}" required>
            </div>

{audio_fields}{visual_fields}{message_expiration_field_html(expiration_messages)}            <div class="form-group">
                <label class="main-label" for="priority">Priority</label>
                <select name="priority" id="priority" class="form-control">
                    <option value="Low">Low</option>
                    <option value="Normal" selected>Normal</option>
                    <option value="High">High</option>
                    <option value="Emergency">Emergency</option>
                </select>
            </div>

{vendor_specific_html}
            <div class="form-actions">
                <button type="submit" class="btn-primary">Create Message</button>
                <a href="/messages/" style="color:#777; text-decoration:none;">Cancel</a>
            </div>
        </form>
    </div>
{message_variable_guide_html()}"""

    return legacy_page("New Message", ctx, "messages", MESSAGE_FORM_STYLE, content, MESSAGE_FORM_SCRIPT)
