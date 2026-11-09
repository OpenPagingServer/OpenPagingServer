from srv.web.app import *
from srv.web.pages.bells.bell_helpers import bells_demo_return, bells_page, schedule_or_404, schedule_settings_card


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ensure_bell_schema()
    sid = int(request.values.get("id", "0") or 0)
    if sid <= 0:
        return redirect("/bells")
    if demo_mode_enabled() and request.method == "POST":
        return bells_demo_return()
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "duplicate":
            if not can_create_bell_schedules(user):
                abort(403)
            source = schedule_or_404(sid)
            name = request.form.get("name", "").strip()
            if not name:
                return redirect("/bells")
            conn = db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO bell_schedules (name, enabled, timezone) VALUES (%s,0,%s)",
                        (name, source.get("timezone") or "server"),
                    )
                    new_schedule_id = cur.lastrowid
                    cur.execute(
                        """
                        INSERT INTO bell_schedule_groups (schedule_id, group_id)
                        SELECT %s, group_id FROM bell_schedule_groups WHERE schedule_id=%s
                        """,
                        (new_schedule_id, sid),
                    )
                    cur.execute("SELECT id, name FROM bell_lists WHERE schedule_id=%s ORDER BY id", (sid,))
                    list_id_map = {}
                    for source_list in cur.fetchall():
                        cur.execute(
                            "INSERT INTO bell_lists (schedule_id, name) VALUES (%s,%s)",
                            (new_schedule_id, source_list["name"]),
                        )
                        new_list_id = cur.lastrowid
                        list_id_map[int(source_list["id"])] = int(new_list_id)
                        cur.execute(
                            """
                            INSERT INTO bell_events (list_id, fire_time, audio, days_of_week)
                            SELECT %s, fire_time, audio, days_of_week FROM bell_events WHERE list_id=%s
                            """,
                            (new_list_id, source_list["id"]),
                        )
                    cur.execute(
                        "SELECT bell_date, list_id FROM bell_calendar_lists WHERE schedule_id=%s",
                        (sid,),
                    )
                    for calendar_row in cur.fetchall():
                        source_list_id = int(calendar_row["list_id"])
                        copied_list_id = list_id_map.get(source_list_id, source_list_id)
                        cur.execute(
                            "INSERT INTO bell_calendar_lists (schedule_id, bell_date, list_id) VALUES (%s,%s,%s)",
                            (new_schedule_id, calendar_row["bell_date"], copied_list_id),
                        )
                conn.commit()
            finally:
                conn.close()
            return redirect("/bells/edit?" + urlencode({"id": new_schedule_id}))
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
<form class="card" method="POST" action="/bells/edit" onsubmit="{"openDemoModePopup('bells'); return false;" if demo_mode_enabled() else "return confirm('Delete this schedule?')"}">
    <input type="hidden" name="id" value="{h(sid)}">
    <input type="hidden" name="action" value="delete">
    <button class="btn danger" type="submit"><i class="fa-solid fa-trash"></i> Delete Schedule</button>
</form>"""
    return bells_page("Edit Schedule", schedule["name"], actions, body, user)
