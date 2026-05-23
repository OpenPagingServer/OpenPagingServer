from srv.web.app import *
from srv.web.pages.bells.bell_helpers import bells_page, schedule_or_404, schedule_settings_card


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ensure_bell_schema()
    sid = int(request.values.get("schedule_id", "0") or 0)
    schedule = schedule_or_404(sid)
    if request.method == "POST":
        execute("DELETE FROM bell_schedule_groups WHERE schedule_id=%s", (sid,))
        for gid in request.form.getlist("groups"):
            execute("INSERT INTO bell_schedule_groups (schedule_id, group_id) VALUES (%s,%s)", (sid, gid))
        return redirect("/bells/groups?" + urlencode({"schedule_id": sid}))
    selected = {str(row["group_id"]) for row in query_all("SELECT group_id FROM bell_schedule_groups WHERE schedule_id=%s", (sid,))}
    groups = query_all("SELECT id, name FROM groups ORDER BY name ASC")
    checks = "".join(f'<label class="weekday-chip"><input type="checkbox" name="groups" value="{h(group["id"])}"{" checked" if str(group["id"]) in selected else ""}> {h(group["name"])}</label>' for group in groups)
    body = f"""<div class="summary-grid">
    <div class="summary-item"><strong>{h(len(selected))}</strong><span class="muted">Selected Groups</span></div>
    <div class="summary-item"><strong>{h(len(groups))}</strong><span class="muted">Available Groups</span></div>
</div>""" + schedule_settings_card(schedule, "groups") + f"""

<form class="card" method="POST" action="/bells/groups">
    <input type="hidden" name="schedule_id" value="{h(sid)}">
    <div class="field"><label>Endpoint groups</label><div class="weekday-row">{checks or '<span class="muted">No groups configured.</span>'}</div></div>
    <button class="btn" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save Groups</button>
</form>"""
    return bells_page("Bell Groups", schedule["name"], '<a class="btn secondary" href="/bells"><i class="fa-solid fa-arrow-left"></i> Back</a>', body, user)
