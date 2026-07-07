from srv.web.app import *
from srv.web.pages.bells.bell_helpers import bells_demo_return, bells_page, timezone_options


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ensure_bell_schema()
    if not can_create_bell_schedules(user):
        abort(403)
    if demo_mode_enabled():
        if request.method == "POST":
            return bells_demo_return()
        return demo_mode_page("New Schedule", legacy_user_context(user), "bells", "bells")
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        enabled = 1 if request.form.get("enabled") else 0
        timezone = request.form.get("timezone", "server").strip() or "server"
        if name:
            schedule_id = execute("INSERT INTO bell_schedules (name, enabled, timezone) VALUES (%s,%s,%s)", (name, enabled, timezone))
            return redirect("/bells/edit?" + urlencode({"id": schedule_id or ""}))
    actions = '<a class="btn secondary" href="/bells"><i class="fa-solid fa-arrow-left"></i> Back</a>'
    body = f"""
<form class="card" method="POST" action="/bells/new">
    <div class="field"><label for="name">Schedule name</label><input id="name" name="name" required autofocus></div>
    <div class="field"><label for="timezone">Time zone</label><select id="timezone" name="timezone">{timezone_options("server")}</select></div>
    <label class="checkbox-row md-checkbox-container"><input type="checkbox" name="enabled" checked><span class="md-checkmark"></span><span class="md-checkbox-text">Enabled</span></label>
    <div class="actions" style="margin-top:12px;"><button class="btn" type="submit"><i class="fa-solid fa-floppy-disk"></i> Create Schedule</button><a class="btn secondary" href="/bells">Cancel</a></div>
</form>"""
    return bells_page("New Schedule", "Create a bell schedule", actions, body, user)
