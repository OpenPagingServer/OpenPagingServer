from srv.web.app import *
from srv.web.pages.bells.bell_helpers import bells_page


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ensure_bell_schema()
    schedules = query_all(
        """
        SELECT
            s.id,
            s.name,
            s.enabled,
            s.timezone,
            COALESCE(l.custom_lists, 0) AS custom_lists,
            COALESCE(g.group_count, 0) AS group_count,
            COALESCE(c.override_days, 0) AS override_days
        FROM bell_schedules s
        LEFT JOIN (
            SELECT schedule_id, COUNT(*) AS custom_lists
            FROM bell_lists
            WHERE schedule_id <> 0
            GROUP BY schedule_id
        ) l ON l.schedule_id = s.id
        LEFT JOIN (
            SELECT schedule_id, COUNT(*) AS group_count
            FROM bell_schedule_groups
            GROUP BY schedule_id
        ) g ON g.schedule_id = s.id
        LEFT JOIN (
            SELECT schedule_id, COUNT(DISTINCT bell_date) AS override_days
            FROM bell_calendar_lists
            GROUP BY schedule_id
        ) c ON c.schedule_id = s.id
        ORDER BY s.enabled DESC, s.name ASC
        """
    )
    enabled_count = sum(1 for row in schedules if int(row.get("enabled") or 0) == 1)
    list_row = query_one("SELECT COUNT(*) AS total FROM bell_lists WHERE schedule_id = 0")
    list_count = int((list_row or {}).get("total") or 0)
    exception_row = query_one("SELECT COUNT(DISTINCT bell_date) AS total FROM bell_calendar_lists")
    exception_count = int((exception_row or {}).get("total") or 0)
    demo = demo_mode_enabled()
    actions = '<a class="btn secondary" href="/bells/bell-lists"><i class="fa-solid fa-list-check"></i> System Lists</a><a class="btn" href="' + ("javascript:openDemoModePopup('bells')" if demo else "/bells/new") + '"><i class="fa-solid fa-plus"></i> New Schedule</a>'
    rows = []
    for schedule in schedules:
        enabled = int(schedule.get("enabled") or 0) == 1
        timezone_name = schedule.get("timezone") or "server"
        rows.append(
            f"""<li class="list-item">
                    <div class="list-main">
                        <div class="list-title">{h(schedule["name"])}</div>
                        <div class="list-meta">{"Enabled" if enabled else "Disabled"}</div>
                    </div>
                    <div class="actions">
                        <a class="btn secondary" href="/bells/calendar?{h(urlencode({'schedule_id': schedule['id']}))}"><i class="fa-solid fa-calendar-days"></i> Calendar</a>
                        <a class="btn secondary" href="/bells/lists?{h(urlencode({'schedule_id': schedule['id']}))}"><i class="fa-solid fa-bell"></i> Bells</a>
                        <a class="btn secondary" href="/bells/groups?{h(urlencode({'schedule_id': schedule['id']}))}"><i class="fa-solid fa-user-group"></i> Groups</a>
                        <a class="btn icon secondary" href="{"javascript:openDemoModePopup('bells')" if demo else f"/bells/edit?{h(urlencode({'id': schedule['id']}))}"}" title="Edit Schedule"><i class="fa-solid fa-pen-to-square"></i></a>
                    </div>
                </li>"""
        )
    schedule_list = '<p class="muted" style="text-align:center;padding:20px;">No bell schedules yet.</p>' if not rows else f'<ul class="list">{"".join(rows)}</ul>'
    body = f"""<div class="summary-grid">
    <div class="summary-item"><strong>{h(len(schedules))}</strong><span class="muted">Schedules</span></div>
    <div class="summary-item"><strong>{h(enabled_count)}</strong><span class="muted">Enabled</span></div>
    <a class="summary-item" href="/bells/bell-lists" style="text-decoration:none;color:inherit;"><strong>{h(list_count)}</strong><span class="muted">System Bell Lists</span></a>
    <div class="summary-item"><strong>{h(exception_count)}</strong><span class="muted">Calendar Override Days</span></div>
</div>
<div class="info-card flush">{schedule_list}</div>"""
    return bells_page("Bells", "Schedules, lists, and calendar exceptions", actions, body, user)
