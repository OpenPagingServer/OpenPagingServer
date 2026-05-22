"""Python web page implementation."""

from srv.web.app import *
from srv.web.pages.messages.form_common import (
    MESSAGE_FORM_SCRIPT,
    MESSAGE_FORM_STYLE,
    audio_transfer_html,
    message_multiline_text,
)

def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ctx = legacy_user_context(user)
    if not ctx["is_admin"]:
        abort(403)

    if request.method == "POST":
        audio = ":".join(request.form.getlist("audio_files[]"))
        values = {
            "messageid": next_message_id(),
            "name": request.form.get("name", ""),
            "type": request.form.get("type", ""),
            "shortmessage": request.form.get("shortmessage", ""),
            "longmessage": message_multiline_text(request.form.get("longmessage", "")),
            "color": request.form.get("color", "").lstrip("#"),
            "audio": audio,
            "expires": request.form.get("expires", "manual").strip() or "manual",
        }
        columns = table_columns("messages")
        wanted = ["messageid", "name", "type", "shortmessage", "longmessage", "color", "audio", "expires"]
        insert = {k: values.get(k, "") for k in wanted if k in columns}
        execute(
            f"INSERT INTO messages ({', '.join('`'+k+'`' for k in insert)}) VALUES ({', '.join(['%s'] * len(insert))})",
            tuple(insert.values()),
        )
        return redirect("/messages/")

    transfer = audio_transfer_html(audio_files())
    content = f"""    <div class="header-actions">
        <h1>New Message</h1>
    </div>

    <div class="info-card">
        <form method="POST">

            <div class="form-group">
                <label class="main-label" for="name">Name</label>
                <p class="help-text">Enter the name of the message. It will be shown in the interface, and may show up on certain endpoints.</p>
                <input type="text" name="name" id="name" class="form-control" required>
            </div>

            <div class="form-group">
                <label class="main-label">Message Type</label>
                <p class="help-text">Select the type of message</p>
                <div class="radio-group">
                    <label>
                        <input type="radio" name="type" value="text+audio" onchange="toggleFields()" required> Audio & visual message (audio+text)
                    </label>
                    <label>
                        <input type="radio" name="type" value="audio" onchange="toggleFields()"> Audio message (audio)
                    </label>
                    <label>
                        <input type="radio" name="type" value="text" onchange="toggleFields()"> Visual message (text)
                    </label>
                </div>
            </div>

            <div id="visual-fields" style="display:none;">
                <div class="form-group">
                    <label class="main-label" for="shortmessage">Short Message</label>
                    <p class="help-text">Enter the short text message. Usually shown on previews and on wall-mounted devices. This should be brief. You can use variables.</p>
                    <input type="text" name="shortmessage" id="shortmessage" class="form-control">
                </div>

                <div class="form-group">
                    <label class="main-label" for="longmessage">Long Message</label>
                    <p class="help-text">Enter the long text message. Usually shown on apps, and in a "more details" section. This should contain as much information as a user would need to know about the situation or incident associated with the message.</p>
                    <textarea name="longmessage" id="longmessage" class="form-control textarea-long" rows="7" wrap="soft"></textarea>
                </div>

                <div class="form-group">
                    <label class="main-label">Color</label>
                    <p class="help-text">Certain endpoints can show a color-coded message.</p>
                    <div class="color-picker-container">
                        <input type="color" id="colorPicker" value="#000000" class="color-picker-input">
                        <input type="text" name="color" id="colorHex" class="form-control" style="width: 150px;" placeholder="000000" maxlength="6">
                    </div>
                </div>
            </div>

            <div id="audio-fields" style="display:none;" class="form-group">
                <label class="main-label">Audio</label>
                <p class="help-text">Select audio files to include in this message. The files will play in the order listed in the selected column. You can click to select and use buttons, or drag and drop to move and reorder.</p>
                {transfer}
            </div>

            <div class="form-group">
                <label class="main-label" for="expires">Expiration</label>
                <p class="help-text">Use 30m or 15m, msg=3 or msg=3.4, or manual for no automatic expiration.</p>
                <input type="text" name="expires" id="expires" class="form-control" value="manual">
            </div>

            <div style="margin-top: 20px;">
                <button type="submit" class="btn-primary">Create Message</button>
                <a href="/messages/" style="margin-left:10px; color:#777; text-decoration:none;">Cancel</a>
            </div>
        </form>
    </div>"""
    return legacy_page("New Message", ctx, "messages", MESSAGE_FORM_STYLE, content, MESSAGE_FORM_SCRIPT)
