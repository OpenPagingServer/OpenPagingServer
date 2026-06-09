from zoneinfo import available_timezones

from srv.web.app import *

BELL_STYLE = r"""
body,html{margin:0;padding:0;font-family:"Tahoma",sans-serif;font-weight:300;background:#FFF;height:100%;}
#sidebar{width:220px;background:#1976D2;color:#FFF;height:100vh;position:fixed;top:0;left:0;display:flex;flex-direction:column;box-shadow:2px 0 8px rgba(0,0,0,.2);transition:transform .3s ease;z-index:1200;}
@media(max-width:767px){#sidebar{transform:translateX(-100%);}#sidebar.open{transform:translateX(0);}}
#sidebar h2{text-align:center;padding:20px 0;margin:0;font-weight:500;background:#1565C0;font-size:1.2em;color:#FFF;}
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{color:#FFF;padding:12px 20px;display:block;border-bottom:1px solid rgba(255,255,255,.1);text-decoration:none;transition:background .3s;font-size:.9em;text-align:left;box-sizing:border-box;}
#sidebar a i,.logout-btn i,.logout-btn-mobile i,.admin-only i{margin-right:8px;width:20px;}
#sidebar a:hover,#sidebar a.active{background:#1565C0;}
.logout-btn{background:#C62828;border:none;cursor:pointer;margin-top:auto;transition:background-color .3s;}
.logout-btn-mobile{background:#C62828;border:none;cursor:pointer;transition:background-color .3s;display:none;}
@media(max-width:767px){.logout-btn{display:none;}.logout-btn-mobile{display:block;}}
#mobile-header{display:flex;background:#1565C0;color:#FFF;padding:calc(12px + env(safe-area-inset-top)) 16px 12px;align-items:center;justify-content:space-between;position:fixed;top:0;left:0;right:0;z-index:1100;}
#mobile-header h2{margin:0;font-size:1.1em;font-weight:400;color:#FFF;}
#mobile-header .hamburger{font-size:1.5em;cursor:pointer;}
#overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.3);z-index:900;}
#overlay.active{display:block;}
#content{margin-left:220px;padding:24px;min-height:100vh;width:calc(100% - 220px);box-sizing:border-box;}
@media(max-width:767px){#content{margin-left:0;width:100%;padding-top:70px;}}
@media(min-width:768px){#mobile-header{display:none;}}
h1{font-weight:400;margin:0;}
.header-actions{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;gap:16px;flex-wrap:wrap;}
.header-main{display:flex;flex-direction:column;gap:5px;}
.server-clock{text-align:right;color:#444;font-size:.96em;line-height:1.4;min-width:220px;}
.server-clock strong{display:block;font-size:1.08em;color:#202124;}
.btn{background:#1976D2;color:#FFF;border:none;border-radius:4px;padding:10px 12px;cursor:pointer;font:inherit;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:8px;white-space:nowrap;min-height:38px;box-sizing:border-box;}
.btn.icon{width:38px;height:38px;padding:0;flex:0 0 auto;}
.btn.secondary{background:transparent;color:#1976D2;}
.btn.danger{background:#C62828;}
.actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:16px;}
.summary-item{border:1px solid #EEE;border-radius:8px;padding:12px;background:#FFF;}
.summary-item strong{display:block;font-size:1.4em;font-weight:500;}
.info-card,.card{background:#FFF;border:1px solid #EEE;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.1);padding:16px;margin-bottom:16px;}
.info-card.flush{padding:0;overflow:hidden;}
.list{list-style:none;margin:0;padding:0;}
.list-item{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 18px;border-bottom:1px solid #EEE;background:#FFF;}
.list-item:last-child{border-bottom:none;}
.list-main{min-width:0;}
.list-title{font-size:1.05em;font-weight:500;color:#202124;overflow-wrap:anywhere;}
.list-meta{color:#555;margin-top:4px;font-size:.92em;}
.field{display:flex;flex-direction:column;gap:6px;margin-bottom:14px;}
.field label{font-size:.9em;color:#555;font-weight:500;}
input,select{border:1px solid #CCC;border-radius:4px;padding:10px;font:inherit;background:#FFF;color:#202124;box-sizing:border-box;width:100%;}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;}
.row>*{flex:1;}
.checkbox-row{display:flex;align-items:center;gap:8px;padding:7px 0;}
.checkbox-row input{width:auto;}
.muted{color:#777;font-size:.9em;}
.error{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:12px;border-radius:8px;margin-bottom:16px;}
.calendar-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;}
.calendar-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;}
.dow{text-align:center;color:#666;font-size:.82em;font-weight:500;padding:4px;}
.day{border:1px solid #EEE;border-radius:6px;min-height:126px;padding:7px;background:#FFF;display:flex;flex-direction:column;gap:6px;}
.day.empty{background:transparent;border-color:transparent;}
.day-number{font-weight:500;color:#333;display:flex;align-items:center;justify-content:space-between;gap:6px;}
.day-list{display:flex;align-items:center;justify-content:space-between;gap:6px;border:1px solid #EEE;border-radius:4px;padding:5px 6px;font-size:.86em;background:#FAFAFA;}
.day-add{display:none;margin-top:2px;}
.day.adding .day-add{display:block;}
.day-add .row{gap:5px;}
.bulk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;align-items:end;}
.event{display:flex;align-items:center;justify-content:space-between;gap:10px;border-bottom:1px solid #EEE;padding:10px 0;}
.event:last-child{border-bottom:none;}
.event-main{min-width:0;overflow-wrap:anywhere;}
.bell-list-card{padding:0;overflow:hidden;}
.bell-list-card>.list-editor-head{padding:16px 18px;border-bottom:1px solid #EEE;}
.list-editor-head{display:flex;align-items:center;justify-content:space-between;gap:16px;}
.compact-form{padding:14px;margin:0;border-left:0;border-right:0;border-bottom:0;border-radius:0;box-shadow:none;}
.schedule-settings-card{padding:14px 16px;}
.schedule-settings-grid{display:grid;grid-template-columns:minmax(220px,1fr) minmax(220px,1fr) auto auto;gap:12px;align-items:end;}
.schedule-settings-grid .field{margin-bottom:0;}
.schedule-enabled{padding:10px 0;margin:0;align-self:end;white-space:nowrap;}
.schedule-tabs{display:flex;gap:8px;flex-wrap:wrap;border-top:1px solid #EEE;margin-top:14px;padding-top:12px;}
.schedule-tabs a{color:#1976D2;text-decoration:none;padding:8px 10px;border-radius:4px;display:inline-flex;align-items:center;gap:7px;font-size:.94em;}
.schedule-tabs a.active,.schedule-tabs a:hover{background:#E3F2FD;color:#1565C0;}
.weekday-row{display:flex;flex-wrap:wrap;gap:6px;}
.weekday-chip{display:inline-flex;align-items:center;gap:5px;border:1px solid #DDD;border-radius:4px;padding:6px 8px;background:#FAFAFA;font-size:.9em;}
@media(max-width:980px){.schedule-settings-grid{grid-template-columns:1fr 1fr;}.schedule-enabled{align-self:center;}}
@media(max-width:620px){.schedule-settings-grid{grid-template-columns:1fr;}.server-clock{text-align:left;}}
@media(prefers-color-scheme:dark){
body,html{background:#121212;color:#E0E0E0;}
#sidebar,#mobile-header{background:#424242;}
#sidebar h2{background:#303030;color:#FFF;}
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{color:#E0E0E0;}
#sidebar a.active,#sidebar a:hover{background:#505050;}
#content{background:#121212;}
.server-clock,.field label,.muted,.dow{color:#BBB;}
.server-clock strong,.list-title,.day-number{color:#EDEDED;}
.info-card,.card,.summary-item,.list-item,.day{border-color:#333;background:#1E1E1E;}
.list-item,.event{border-bottom-color:#333;}
.list-meta{color:#CCC;}
input,select{background:#121212;border-color:#444;color:#E0E0E0;}
.btn{background:#BB86FC;color:#000;}
.btn.secondary{background:transparent;color:#BB86FC;}
.btn.danger{background:#CF6679;color:#000;}
.error{background:#3B1515;border-color:#6D2A2A;color:#FFCDD2;}
.day-list{background:#242424;border-color:#333;}
.bell-list-card>.list-editor-head{border-bottom-color:#333;}
.weekday-chip{background:#242424;border-color:#444;}
.schedule-tabs{border-top-color:#333;}
.schedule-tabs a{color:#BB86FC;}
.schedule-tabs a.active,.schedule-tabs a:hover{background:#2A2433;color:#D9B8FF;}
}
"""


def bell_header(title, subtitle="", actions=""):
    return f"""<div class="header-actions">
    <div class="header-main">
        <h1>{h(title)}</h1>
        {f'<div class="muted">{h(subtitle)}</div>' if subtitle else ''}
    </div>
    <div class="server-clock">
        <span id="systemDate">Loading server date...</span>
        <strong id="systemTime">Loading server time...</strong>
    </div>
    {f'<div class="actions">{actions}</div>' if actions else ''}
</div>"""


def bell_clock_script():
    return """
<script>
let bellsClock = { timestamp: null, uses12Hour: false, timezone: undefined };
function renderBellClock() {
  if (bellsClock.timestamp === null) return;
  const now = new Date(bellsClock.timestamp);
  document.getElementById('systemDate').textContent = now.toLocaleDateString(undefined, { weekday:'long', year:'numeric', month:'long', day:'numeric', timeZone:bellsClock.timezone });
  document.getElementById('systemTime').textContent = now.toLocaleTimeString(undefined, { hour:'numeric', minute:'2-digit', second:'2-digit', hour12:bellsClock.uses12Hour, timeZone:bellsClock.timezone });
  bellsClock.timestamp += 1000;
}
async function refreshServerClock(){
  try {
    const response = await fetch('/bells/time', { cache: 'no-store' });
    const data = await response.json();
    bellsClock.timestamp = data.timestamp_ms;
    bellsClock.uses12Hour = !!data.uses_12_hour;
    bellsClock.timezone = data.timezone || undefined;
    renderBellClock();
  } catch (error) {
    document.getElementById('systemDate').textContent = 'Server date unavailable';
    document.getElementById('systemTime').textContent = 'Server time unavailable';
  }
}
refreshServerClock();
setInterval(renderBellClock, 1000);
setInterval(refreshServerClock, 10000);
</script>"""


def bells_page(title, subtitle, actions, body, user, status=200):
    response = legacy_page(title, legacy_user_context(user), "bells", BELL_STYLE, bell_header(title, subtitle, actions) + body + bell_clock_script())
    response.status_code = status
    return response


def bells_demo_return():
    return demo_mode_iframe_html("bells")


def timezone_options(selected="server"):
    selected = selected or "server"
    zones = ["server", "UTC"] + sorted(z for z in available_timezones() if z != "UTC")
    labels = {"server": "Server default"}
    return "".join(f'<option value="{h(zone)}"{" selected" if zone == selected else ""}>{h(labels.get(zone, zone))}</option>' for zone in zones)


def weekday_short_names():
    return {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat"}


def days_summary(value):
    names = weekday_short_names()
    days = [day for day in str(value or "").split(",") if day in names]
    if len(days) == 7:
        return "Every day"
    if days == ["1", "2", "3", "4", "5"]:
        return "Weekdays"
    if days == ["0", "6"]:
        return "Weekends"
    return ", ".join(names[day] for day in days) if days else "No days selected"


def schedule_settings_card(schedule, active_tab="settings", return_to=""):
    sid = schedule["id"]
    demo = demo_mode_enabled()
    tabs = [
        ("calendar", "Calendar", f"/bells/calendar?{urlencode({'schedule_id': sid})}", "fa-calendar-days"),
        ("bells", "Bells", f"/bells/lists?{urlencode({'schedule_id': sid})}", "fa-bell"),
        ("groups", "Groups", f"/bells/groups?{urlencode({'schedule_id': sid})}", "fa-user-group"),
    ]
    tab_links = "".join(f'<a class="{"active" if active_tab == key else ""}" href="{h(url)}"><i class="fa-solid {icon}"></i> {h(label)}</a>' for key, label, url, icon in tabs)
    onsubmit_attr = " onsubmit=\"openDemoModePopup('bells'); return false;\"" if demo else ""
    return_to_value = h(return_to or request.full_path.rstrip("?"))
    schedule_name = h(schedule.get("name") or "")
    schedule_timezone = timezone_options(schedule.get("timezone") or "server")
    enabled_checked = " checked" if int(schedule.get("enabled") or 0) == 1 else ""
    return f"""<form class="card schedule-settings-card" method="POST" action="/bells/edit"{onsubmit_attr}>
    <input type="hidden" name="id" value="{h(sid)}">
    <input type="hidden" name="action" value="save">
    <input type="hidden" name="return_to" value="{return_to_value}">
    <div class="schedule-settings-grid">
        <div class="field"><label for="schedule_name">Schedule name</label><input id="schedule_name" name="name" value="{schedule_name}" required></div>
        <div class="field"><label for="schedule_timezone">Time zone</label><select id="schedule_timezone" name="timezone">{schedule_timezone}</select></div>
        <label class="checkbox-row schedule-enabled"><input type="checkbox" name="enabled"{enabled_checked}><span>Enabled</span></label>
        <button class="btn" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save Schedule</button>
    </div>
    <div class="schedule-tabs">{tab_links}</div>
</form>"""


def schedule_or_404(schedule_id):
    row = query_one("SELECT id, name, enabled, timezone FROM bell_schedules WHERE id=%s LIMIT 1", (schedule_id,))
    if not row:
        abort(404)
    return row
