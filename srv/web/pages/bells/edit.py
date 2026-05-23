from srv.web.app import *
from srv.web.pages.bells.bell_helpers import bells_page, schedule_or_404, schedule_settings_card


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ensure_bell_schema()
    sid = int(request.values.get("id", "0") or 0)
    if sid <= 0:
        return redirect("/bells")
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            for table in ("bell_schedule_groups", "bell_calendar_lists", "bell_lists"):
                execute(f"DELETE FROM `{table}` WHERE schedule_id=%s", (sid,))
            execute("DELETE FROM bell_events WHERE list_id NOT IN (SELECT id FROM bell_lists)")
            execute("DELETE FROM bell_schedules WHERE id=%s", (sid,))
            return redirect("/bells")
        name = request.form.get("name", "").strip()
        enabled = 1 if request.form.get("enabled") else 0
        timezone = request.form.get("timezone", "server").strip() or "server"
        if name:
            execute("UPDATE bell_schedules SET name=%s, enabled=%s, timezone=%s WHERE id=%s", (name, enabled, timezone, sid))
        return_to = request.form.get("return_to", "").strip()
        if return_to.startswith("/bells/"):
            return redirect(return_to)
        return redirect("/bells/edit?" + urlencode({"id": sid}))
    schedule = schedule_or_404(sid)
    actions = '<a class="btn secondary" href="/bells"><i class="fa-solid fa-arrow-left"></i> Back</a>'
    counts = query_one(
        """
        SELECT
            (SELECT COUNT(*) FROM bell_lists WHERE schedule_id=%s) AS custom_lists,
            (SELECT COUNT(*) FROM bell_schedule_groups WHERE schedule_id=%s) AS groups,
            (SELECT COUNT(DISTINCT bell_date) FROM bell_calendar_lists WHERE schedule_id=%s) AS override_days
        """,
        (sid, sid, sid),
    ) or {}
    body = f"""<div class="summary-grid">
    <div class="summary-item"><strong>{h(counts.get("custom_lists") or 0)}</strong><span class="muted">Custom Lists</span></div>
    <div class="summary-item"><strong>{h(counts.get("groups") or 0)}</strong><span class="muted">Assigned Groups</span></div>
    <div class="summary-item"><strong>{h(counts.get("override_days") or 0)}</strong><span class="muted">Override Days</span></div>
</div>""" + schedule_settings_card(schedule, "settings") + f"""
<form class="card" method="POST" action="/bells/edit" onsubmit="return confirm('Delete this schedule?')">
    <input type="hidden" name="id" value="{h(sid)}">
    <input type="hidden" name="action" value="delete">
    <button class="btn danger" type="submit"><i class="fa-solid fa-trash"></i> Delete Schedule</button>
</form>"""
    return bells_page("Edit Schedule", schedule["name"], actions, body, user)
