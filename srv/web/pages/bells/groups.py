from srv.web.app import *
from srv.web.pages.bells.bell_helpers import bells_demo_return, bells_page, schedule_or_404, schedule_settings_card


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user

    ensure_bell_schema()

    sid = int(request.values.get("schedule_id", "0") or 0)
    schedule = schedule_or_404(sid)
    demo = demo_mode_enabled()
    demo_submit = ' onsubmit="openDemoModePopup(\'bells\'); return false;"' if demo else ""

    if demo and request.method == "POST":
        return bells_demo_return()

    if request.method == "POST":
        execute("DELETE FROM bell_schedule_groups WHERE schedule_id=%s", (sid,))
        for gid in request.form.getlist("groups"):
            execute("INSERT INTO bell_schedule_groups (schedule_id, group_id) VALUES (%s,%s)", (sid, gid))
        return redirect("/bells/groups?" + urlencode({"schedule_id": sid}))

    selected = {str(row["group_id"]) for row in query_all("SELECT group_id FROM bell_schedule_groups WHERE schedule_id=%s", (sid,))}
    groups = query_all("SELECT id, name FROM groups ORDER BY name ASC")

    checks = "".join(
        f'''
        <label style="display:flex;align-items:center;justify-content:flex-start;gap:10px;width:100%;min-height:42px;padding:10px 12px;box-sizing:border-box;text-align:left;border:1px solid var(--border);border-radius:10px;background:var(--card-bg);cursor:pointer;">
            <input type="checkbox" name="groups" value="{h(group["id"])}" style="margin:0;flex:0 0 auto;width:16px;height:16px;"{" checked" if str(group["id"]) in selected else ""}>
            <span style="display:block;flex:1;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{h(group["name"])}</span>
        </label>
        '''
        for group in groups
    )

    body = f"""""" + schedule_settings_card(schedule, "groups") + f"""

<form class="card" method="POST" action="/bells/groups"{demo_submit} style="max-width:420px;width:100%;margin-left:0;margin-right:auto;text-align:left;">
    <input type="hidden" name="schedule_id" value="{h(sid)}">
    <div class="field" style="display:block;width:100%;text-align:left;margin:0 0 16px 0;">
        <div style="display:flex;flex-direction:column;align-items:stretch;justify-content:flex-start;gap:8px;width:100%;max-width:320px;text-align:left;margin:0;">
            {checks or '<span class="muted">No groups configured.</span>'}
        </div>
    </div>
    <button class="btn" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save Groups</button>
</form>"""

    return bells_page("Bell Groups", schedule["name"], '<a class="btn secondary" href="/bells"><i class="fa-solid fa-arrow-left"></i> Back</a>', body, user)
