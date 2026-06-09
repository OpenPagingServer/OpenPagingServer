import calendar
from datetime import date, datetime, timedelta

from srv.web.app import *
from srv.web.pages.bells.bell_helpers import bells_demo_return, bells_page, schedule_or_404, schedule_settings_card, weekday_short_names


def month_value(raw):
    value = str(raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", value):
        return value
    return date.today().strftime("%Y-%m")


def available_lists(schedule_id):
    return query_all("SELECT id, schedule_id, name FROM bell_lists WHERE schedule_id=0 OR schedule_id=%s ORDER BY schedule_id ASC, name ASC", (schedule_id,))


def can_use_list(list_id, schedule_id):
    row = query_one("SELECT COUNT(*) AS total FROM bell_lists WHERE id=%s AND (schedule_id=0 OR schedule_id=%s)", (list_id, schedule_id))
    return int((row or {}).get("total") or 0) > 0


def list_options(lists, include_all=False):
    options = '<option value="0">All assigned lists</option>' if include_all else ""
    for item in lists:
        scope = "System" if int(item.get("schedule_id") or 0) == 0 else "Custom"
        options += f'<option value="{h(item["id"])}">{h(scope + ": " + item["name"])}</option>'
    return options


def weekday_names():
    return weekday_short_names()


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ensure_bell_schema()
    sid = int(request.values.get("schedule_id", "0") or 0)
    schedule = schedule_or_404(sid)
    month = month_value(request.values.get("month"))
    first_day = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    last_day = date(first_day.year, first_day.month, calendar.monthrange(first_day.year, first_day.month)[1])
    demo = demo_mode_enabled()
    demo_submit = ' onsubmit="openDemoModePopup(\'bells\'); return false;"' if demo else ""
    if demo and request.method == "POST":
        return bells_demo_return()
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "add_day_list":
            bell_date = request.form.get("bell_date", "").strip()
            list_id = int(request.form.get("list_id", "0") or 0)
            if list_id > 0 and can_use_list(list_id, sid) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", bell_date):
                execute("INSERT IGNORE INTO bell_calendar_lists (schedule_id, bell_date, list_id) VALUES (%s,%s,%s)", (sid, bell_date, list_id))
        elif action == "remove_day_list":
            bell_date = request.form.get("bell_date", "").strip()
            list_id = int(request.form.get("list_id", "0") or 0)
            if list_id > 0 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", bell_date):
                execute("DELETE FROM bell_calendar_lists WHERE schedule_id=%s AND bell_date=%s AND list_id=%s", (sid, bell_date, list_id))
        elif action in {"bulk_add", "bulk_delete", "bulk_override"}:
            start_date = request.form.get("start_date", "").strip()
            end_date = request.form.get("end_date", "").strip()
            days = set(request.form.getlist("days_of_week"))
            list_id = int(request.form.get("list_id", "0") or 0)
            target_list_id = int(request.form.get("target_list_id", "0") or 0)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date) and days:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
                if end_dt < start_dt:
                    start_dt, end_dt = end_dt, start_dt
                current = start_dt
                while current <= end_dt:
                    if str((current.weekday() + 1) % 7) in days:
                        current_text = current.strftime("%Y-%m-%d")
                        if action == "bulk_add" and list_id > 0 and can_use_list(list_id, sid):
                            execute("INSERT IGNORE INTO bell_calendar_lists (schedule_id, bell_date, list_id) VALUES (%s,%s,%s)", (sid, current_text, list_id))
                        elif action == "bulk_delete" and list_id > 0 and can_use_list(list_id, sid):
                            execute("DELETE FROM bell_calendar_lists WHERE schedule_id=%s AND bell_date=%s AND list_id=%s", (sid, current_text, list_id))
                        elif action == "bulk_delete":
                            execute("DELETE FROM bell_calendar_lists WHERE schedule_id=%s AND bell_date=%s", (sid, current_text))
                        elif action == "bulk_override" and target_list_id > 0 and can_use_list(target_list_id, sid):
                            if list_id > 0 and can_use_list(list_id, sid):
                                execute("DELETE FROM bell_calendar_lists WHERE schedule_id=%s AND bell_date=%s AND list_id=%s", (sid, current_text, list_id))
                            else:
                                execute("DELETE FROM bell_calendar_lists WHERE schedule_id=%s AND bell_date=%s", (sid, current_text))
                            execute("INSERT IGNORE INTO bell_calendar_lists (schedule_id, bell_date, list_id) VALUES (%s,%s,%s)", (sid, current_text, target_list_id))
                    current += timedelta(days=1)
        return redirect("/bells/calendar?" + urlencode({"schedule_id": sid, "month": month}))
    lists = available_lists(sid)
    assignments = {}
    rows = query_all(
        "SELECT c.bell_date, c.list_id, l.name, l.schedule_id FROM bell_calendar_lists c JOIN bell_lists l ON l.id=c.list_id WHERE c.schedule_id=%s AND (l.schedule_id=0 OR l.schedule_id=%s) AND c.bell_date BETWEEN %s AND %s ORDER BY c.bell_date ASC, l.schedule_id ASC, l.name ASC",
        (sid, sid, first_day.strftime("%Y-%m-%d"), last_day.strftime("%Y-%m-%d")),
    )
    for row in rows:
        assignments.setdefault(str(row["bell_date"]), []).append(row)
    prev_month = (first_day - timedelta(days=1)).strftime("%Y-%m")
    next_month = (last_day + timedelta(days=1)).strftime("%Y-%m")
    prev_url = urlencode({"schedule_id": sid, "month": prev_month})
    next_url = urlencode({"schedule_id": sid, "month": next_month})
    back_url = urlencode({"id": sid})
    day_checks = "".join(f'<label class="weekday-chip"><input type="checkbox" name="days_of_week" value="{h(key)}" checked><span>{h(label)}</span></label>' for key, label in weekday_names().items())
    bulk = f"""<div class="card">
        <form method="POST" action="/bells/calendar" class="bulk-grid" style="margin-top:12px;"{demo_submit}>
        <input type="hidden" name="schedule_id" value="{h(sid)}"><input type="hidden" name="month" value="{h(month)}">
        <div class="field"><label for="start_date">Start date</label><input id="start_date" type="date" name="start_date" value="{h(first_day)}" required></div>
        <div class="field"><label for="end_date">End date</label><input id="end_date" type="date" name="end_date" value="{h(last_day)}" required></div>
        <div class="field"><label for="bulk_list_id">List</label><select id="bulk_list_id" name="list_id">{list_options(lists, True)}</select></div>
        <div class="field"><label for="target_list_id">Override to</label><select id="target_list_id" name="target_list_id"><option value="">Choose target list</option>{list_options(lists, False)}</select></div>
        <div class="field"><label>Days</label><div class="weekday-row">{day_checks}</div></div>
        <div class="actions"><button class="btn" type="submit" name="action" value="bulk_add"><i class="fa-solid fa-plus"></i> Add</button><button class="btn danger" type="submit" name="action" value="bulk_delete"><i class="fa-solid fa-trash"></i> Delete</button><button class="btn secondary" type="submit" name="action" value="bulk_override"><i class="fa-solid fa-repeat"></i> Override</button></div>
    </form></div>"""
    cells = "".join(f'<div class="dow">{h(name)}</div>' for name in ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
    cells += '<div class="day empty"></div>' * ((first_day.weekday() + 1) % 7)
    for day_num in range(1, last_day.day + 1):
        current_date = f"{month}-{day_num:02d}"
        assigned_html = ""
        for item in assignments.get(current_date, []):
            scope = "System" if int(item.get("schedule_id") or 0) == 0 else "Custom"
            item_name = item["name"]
            item_list_id = item["list_id"]
            assigned_html += f"""<div class="day-list"><span>{h(scope + ": " + item_name)}</span><form method="POST" action="/bells/calendar"{demo_submit}><input type="hidden" name="action" value="remove_day_list"><input type="hidden" name="schedule_id" value="{h(sid)}"><input type="hidden" name="month" value="{h(month)}"><input type="hidden" name="bell_date" value="{h(current_date)}"><input type="hidden" name="list_id" value="{h(item_list_id)}"><button class="btn icon danger" type="submit" title="Remove"><i class="fa-solid fa-xmark"></i></button></form></div>"""
        if not assigned_html:
            assigned_html = '<div class="muted">Uses the normal schedule.</div>'
        cells += f"""<div class="day">
            <div class="day-number" id="day-{h(current_date)}"><span>{h(day_num)}</span><button class="btn icon secondary" type="button" onclick="toggleDayAdd(this)" title="Add List"><i class="fa-solid fa-plus"></i></button></div>
            {assigned_html}
            <form method="POST" action="/bells/calendar" class="day-add"{demo_submit}><input type="hidden" name="action" value="add_day_list"><input type="hidden" name="schedule_id" value="{h(sid)}"><input type="hidden" name="month" value="{h(month)}"><input type="hidden" name="bell_date" value="{h(current_date)}"><div class="row"><select name="list_id" required><option value="">List</option>{list_options(lists, False)}</select><button class="btn icon" type="submit" title="Add"><i class="fa-solid fa-check"></i></button></div></form>
        </div>"""
    calendar_html = f"""<div class="card">
        <div class="calendar-head"><a class="btn secondary" href="/bells/calendar?{h(prev_url)}"><i class="fa-solid fa-angle-left"></i></a><strong>{h(first_day.strftime("%B %Y"))}</strong><a class="btn secondary" href="/bells/calendar?{h(next_url)}"><i class="fa-solid fa-angle-right"></i></a></div>
        <div class="calendar-grid">{cells}</div>
    </div><script>function toggleDayAdd(button) {{ button.closest('.day').classList.toggle('adding'); }}</script>"""
    body = schedule_settings_card(schedule, "calendar") + bulk + calendar_html
    return bells_page("Bell Calendar", schedule["name"], f'<a class="btn secondary" href="/bells/edit?{h(back_url)}"><i class="fa-solid fa-arrow-left"></i> Back</a>', body, user)
