from srv.web.app import *
from srv.web.pages.bells.bell_helpers import bells_demo_return, bells_page, days_summary, schedule_or_404, schedule_settings_card
import re
from urllib.parse import urlencode

def audio_files():
    try:
        files = [p.name for p in ASSET_DIR.iterdir() if p.is_file() and p.suffix.lower() in {".wav", ".mp3", ".ogg"}]
    except Exception:
        files = []
    return sorted(files, key=str.lower)

def normalize_days(days):
    allowed = ["0", "1", "2", "3", "4", "5", "6"]
    selected = [day for day in allowed if day in set(days or [])]
    return ",".join(selected or allowed)

def render_bells_ui(scope_schedule_id, post_path, hidden_fields, demo=False):
    lists = query_all("SELECT id, schedule_id, name FROM bell_lists WHERE schedule_id=%s ORDER BY name ASC", (scope_schedule_id,))
    events = {}
    if lists:
        ids = [row["id"] for row in lists]
        placeholders = ",".join(["%s"] * len(ids))
        for event in query_all(f"SELECT id, list_id, fire_time, audio, days_of_week FROM bell_events WHERE list_id IN ({placeholders}) ORDER BY fire_time ASC, id ASC", ids):
            events.setdefault(int(event["list_id"]), []).append(event)
    
    files = audio_files()
    audio_options = "".join(f'<option value="{h(file)}">{h(file)}</option>' for file in files)
    day_mapping = {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat"}
    
    def render_day_checks(prefix=""):
        return "".join(f'<label class="weekday-chip md-checkbox-container"><input type="checkbox" name="days_of_week" value="{day}" id="{prefix}day_{day}" checked><span class="md-checkmark"></span><span class="md-checkbox-text">{label}</span></label>' for day, label in day_mapping.items())
    
    hidden = "".join(f'<input type="hidden" name="{h(key)}" value="{h(value)}">' for key, value in hidden_fields.items())
    
    main_view = f"""
    <div id="main-view">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
            <h2 style="margin: 0; font-weight: normal;">Bell Lists</h2>
            <button class="btn" type="button" onclick="{"openDemoModePopup('bells')" if demo else "openModal('modal-add-list')"}"><i class="fa-solid fa-plus"></i> Add List</button>
        </div>
        <div class="card" style="padding: 0; overflow: hidden;">
    """
    
    if not lists:
        main_view += '<div style="padding: 25px; text-align: center; opacity: 0.6;">No bell lists found.</div>'
    else:
        for idx, bell_list in enumerate(lists):
            list_id = int(bell_list["id"])
            bell_count = len(events.get(list_id, []))
            border = 'border-bottom: 1px solid var(--border, rgba(128,128,128,0.2));' if idx < len(lists) - 1 else ''
            edit_list_onclick = "openDemoModePopup('bells')" if demo else f"openEditList({list_id}, {json.dumps(str(bell_list['name'] or ''))})"
            
            main_view += f"""
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 15px 20px; {border}">
                <div>
                    <div style="font-size: 1.1em; margin-bottom: 4px;">{h(bell_list["name"])}</div>
                    <div style="font-size: 0.9em; opacity: 0.7;">{bell_count} bells</div>
                </div>
                <div style="display: flex; gap: 8px;">
                    <button class="btn" type="button" onclick="showDetail({list_id})" title="Manage List">
                        <i class="fa-solid fa-list-ul"></i> Manage
                    </button>
                    <button class="btn secondary" type="button" onclick="{h(edit_list_onclick)}" title="Edit List">
                        <i class="fa-solid fa-pen"></i>
                    </button>
                    <form method="POST" action="{h(post_path)}" onsubmit="{"openDemoModePopup('bells'); return false;" if demo else "return confirm('Delete this list and all its bells?')"}" style="margin: 0;">
                        <input type="hidden" name="action" value="delete_list">{hidden}
                        <input type="hidden" name="list_id" value="{list_id}">
                        <button class="btn danger" type="submit" title="Delete List"><i class="fa-solid fa-trash"></i></button>
                    </form>
                </div>
            </div>
            """
    main_view += "</div></div>"
    
    detail_views = '<div id="detail-views-container">'
    for bell_list in lists:
        list_id = int(bell_list["id"])
        list_events = events.get(list_id, [])
        
        detail_views += f"""
        <div id="detail-view-{list_id}" class="detail-view" style="display: none;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
                <div style="display: flex; align-items: center; gap: 15px;">
                    <button class="btn secondary" type="button" onclick="showMain()"><i class="fa-solid fa-arrow-left"></i> Back</button>
                    <h2 style="margin: 0; font-weight: normal;">{h(bell_list["name"])}</h2>
                </div>
                <button class="btn" type="button" onclick="{"openDemoModePopup('bells')" if demo else f"openAddBell({list_id})"}"><i class="fa-solid fa-plus"></i> Add Bell</button>
            </div>
            <div class="card" style="padding: 0; overflow: hidden;">
        """
        if not list_events:
            detail_views += '<div style="padding: 25px; text-align: center; opacity: 0.6;">No bells in this list yet.</div>'
        else:
            for idx, event in enumerate(list_events):
                eid = int(event["id"])
                time_str = h(str(event["fire_time"]))
                audio_str = h(event.get("audio") or "")
                days_str = h(event.get("days_of_week") or "")
                border = 'border-bottom: 1px solid var(--border, rgba(128,128,128,0.2));' if idx < len(list_events) - 1 else ''
                edit_bell_onclick = "openDemoModePopup('bells')" if demo else f"openEditBell({eid}, {json.dumps(str(event['fire_time'] or ''))}, {json.dumps(str(event.get('audio') or ''))}, {json.dumps(str(event.get('days_of_week') or ''))})"
                
                detail_views += f"""
                <div style="display: flex; justify-content: space-between; align-items: center; padding: 15px 20px; {border}">
                    <div>
                        <div style="font-size: 1.1em; margin-bottom: 4px;">{time_str}</div>
                        <div style="font-size: 0.9em; opacity: 0.7;">{h(days_summary(event.get("days_of_week")))} &bull; {audio_str}</div>
                    </div>
                    <div style="display: flex; gap: 8px;">
                        <button class="btn secondary" type="button" onclick="{h(edit_bell_onclick)}" title="Edit Bell">
                            <i class="fa-solid fa-pen"></i>
                        </button>
                        <form method="POST" action="{h(post_path)}" onsubmit="{"openDemoModePopup('bells'); return false;" if demo else "return confirm('Delete this bell?')"}" style="margin: 0;">
                            <input type="hidden" name="action" value="delete_event">{hidden}
                            <input type="hidden" name="event_id" value="{eid}">
                            <button class="btn danger" type="submit" title="Delete Bell"><i class="fa-solid fa-trash"></i></button>
                        </form>
                    </div>
                </div>
                """
        detail_views += "</div></div>"
    detail_views += "</div>"
    
    modal_bg = "display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 9999; padding: 20px; align-items: center; justify-content: center; overflow: auto;"
    modal_card = "width: 100%; max-width: 450px; margin: auto; padding: 25px;"
    input_style = "width: 100%; box-sizing: border-box; padding: 8px; margin-top: 5px;"
    
    modals = f"""
    <div id="modal-add-list" class="bell-modal" style="{modal_bg}">
        <div class="card" style="{modal_card}">
            <h3 style="margin-top: 0; margin-bottom: 20px; font-weight: normal;">Add List</h3>
            <form method="POST" action="{h(post_path)}">
                <input type="hidden" name="action" value="add_list">{hidden}
                <div style="margin-bottom: 20px;">
                    <label>List Name</label>
                    <input type="text" name="list_name" required style="{input_style}">
                </div>
                <div style="display: flex; justify-content: flex-end; gap: 10px;">
                    <button type="button" class="btn secondary" onclick="closeModal('modal-add-list')">Cancel</button>
                    <button type="submit" class="btn">Save</button>
                </div>
            </form>
        </div>
    </div>

    <div id="modal-edit-list" class="bell-modal" style="{modal_bg}">
        <div class="card" style="{modal_card}">
            <h3 style="margin-top: 0; margin-bottom: 20px; font-weight: normal;">Edit List</h3>
            <form method="POST" action="{h(post_path)}">
                <input type="hidden" name="action" value="edit_list">{hidden}
                <input type="hidden" name="list_id" id="edit-list-id" value="">
                <div style="margin-bottom: 20px;">
                    <label>List Name</label>
                    <input type="text" name="list_name" id="edit-list-name" required style="{input_style}">
                </div>
                <div style="display: flex; justify-content: flex-end; gap: 10px;">
                    <button type="button" class="btn secondary" onclick="closeModal('modal-edit-list')">Cancel</button>
                    <button type="submit" class="btn">Save</button>
                </div>
            </form>
        </div>
    </div>

    <div id="modal-add-bell" class="bell-modal" style="{modal_bg}">
        <div class="card" style="{modal_card}">
            <h3 style="margin-top: 0; margin-bottom: 20px; font-weight: normal;">Add Bell</h3>
            <form method="POST" action="{h(post_path)}">
                <input type="hidden" name="action" value="add_event">{hidden}
                <input type="hidden" name="list_id" id="add-bell-list-id" value="">
                <div style="margin-bottom: 15px;">
                    <label>Time</label>
                    <input type="time" name="fire_time" step="1" required style="{input_style}">
                </div>
                <div style="margin-bottom: 15px;">
                    <label>Audio</label>
                    <select name="audio_files" required style="{input_style}">{audio_options}</select>
                </div>
                <div style="margin-bottom: 25px;">
                    <label style="display: block; margin-bottom: 8px;">Days</label>
                    <div style="display: flex; flex-wrap: wrap; gap: 15px;">{render_day_checks("add_")}</div>
                </div>
                <div style="display: flex; justify-content: flex-end; gap: 10px;">
                    <button type="button" class="btn secondary" onclick="closeModal('modal-add-bell')">Cancel</button>
                    <button type="submit" class="btn">Save</button>
                </div>
            </form>
        </div>
    </div>

    <div id="modal-edit-bell" class="bell-modal" style="{modal_bg}">
        <div class="card" style="{modal_card}">
            <h3 style="margin-top: 0; margin-bottom: 20px; font-weight: normal;">Edit Bell</h3>
            <form method="POST" action="{h(post_path)}">
                <input type="hidden" name="action" value="edit_event">{hidden}
                <input type="hidden" name="event_id" id="edit-bell-id" value="">
                <div style="margin-bottom: 15px;">
                    <label>Time</label>
                    <input type="time" name="fire_time" id="edit-bell-time" step="1" required style="{input_style}">
                </div>
                <div style="margin-bottom: 15px;">
                    <label>Audio</label>
                    <select name="audio_files" id="edit-bell-audio" required style="{input_style}">{audio_options}</select>
                </div>
                <div style="margin-bottom: 25px;">
                    <label style="display: block; margin-bottom: 8px;">Days</label>
                    <div style="display: flex; flex-wrap: wrap; gap: 15px;">{render_day_checks("edit_")}</div>
                </div>
                <div style="display: flex; justify-content: flex-end; gap: 10px;">
                    <button type="button" class="btn secondary" onclick="closeModal('modal-edit-bell')">Cancel</button>
                    <button type="submit" class="btn">Save</button>
                </div>
            </form>
        </div>
    </div>
    """
    
    script = """
    <script>
        function openModal(id) {
            document.getElementById(id).style.display = 'flex';
        }
        function closeModal(id) {
            document.getElementById(id).style.display = 'none';
        }
        function showDetail(listId) {
            document.getElementById('main-view').style.display = 'none';
            document.querySelectorAll('.detail-view').forEach(el => el.style.display = 'none');
            document.getElementById('detail-view-' + listId).style.display = 'block';
        }
        function showMain() {
            document.querySelectorAll('.detail-view').forEach(el => el.style.display = 'none');
            document.getElementById('main-view').style.display = 'block';
        }
        function openEditList(id, name) {
            document.getElementById('edit-list-id').value = id;
            document.getElementById('edit-list-name').value = name;
            openModal('modal-edit-list');
        }
        function openAddBell(listId) {
            document.getElementById('add-bell-list-id').value = listId;
            openModal('modal-add-bell');
        }
        function openEditBell(id, time, audio, days) {
            document.getElementById('edit-bell-id').value = id;
            document.getElementById('edit-bell-time').value = time;
            document.getElementById('edit-bell-audio').value = audio;
            
            let selectedDays = days ? days.split(',') : [];
            for (let i = 0; i <= 6; i++) {
                let cb = document.getElementById('edit_day_' + i);
                if (cb) cb.checked = selectedDays.includes(i.toString());
            }
            openModal('modal-edit-bell');
        }
        
        document.querySelectorAll('.bell-modal').forEach(modal => {
            modal.addEventListener('click', function(e) {
                if (e.target === this) {
                    this.style.display = 'none';
                }
            });
        });
    </script>
    """
    
    return main_view + detail_views + modals + script

def handle_list_post(scope_schedule_id):
    action = request.form.get("action", "")
    if action == "delete_list":
        execute("DELETE FROM bell_events WHERE list_id=%s", (request.form.get("list_id"),))
        execute("DELETE FROM bell_lists WHERE id=%s AND schedule_id=%s", (request.form.get("list_id"), scope_schedule_id))
    elif action == "add_list":
        name = request.form.get("list_name", "").strip()
        if name:
            execute("INSERT INTO bell_lists (schedule_id, name) VALUES (%s,%s)", (scope_schedule_id, name))
    elif action == "edit_list":
        name = request.form.get("list_name", "").strip()
        list_id = request.form.get("list_id")
        if name and list_id:
            execute("UPDATE bell_lists SET name=%s WHERE id=%s AND schedule_id=%s", (name, list_id, scope_schedule_id))
    elif action == "add_event":
        list_id = int(request.form.get("list_id", "0") or 0)
        time_value = request.form.get("fire_time", "").strip()
        audio_value = request.form.get("audio_files", "").strip()
        days = normalize_days(request.form.getlist("days_of_week"))
        if re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", time_value) and audio_value:
            if len(time_value) == 5:
                time_value += ":00"
            row = query_one("SELECT COUNT(*) AS total FROM bell_lists WHERE id=%s AND schedule_id=%s", (list_id, scope_schedule_id))
            if int((row or {}).get("total") or 0) > 0:
                execute("INSERT INTO bell_events (list_id, fire_time, audio, days_of_week) VALUES (%s,%s,%s,%s)", (list_id, time_value, audio_value, days))
    elif action == "edit_event":
        event_id = request.form.get("event_id")
        time_value = request.form.get("fire_time", "").strip()
        audio_value = request.form.get("audio_files", "").strip()
        days = normalize_days(request.form.getlist("days_of_week"))
        if re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", time_value) and audio_value and event_id:
            if len(time_value) == 5:
                time_value += ":00"
            execute("UPDATE bell_events e JOIN bell_lists l ON l.id=e.list_id SET e.fire_time=%s, e.audio=%s, e.days_of_week=%s WHERE e.id=%s AND l.schedule_id=%s", (time_value, audio_value, days, event_id, scope_schedule_id))
    elif action == "delete_event":
        execute("DELETE e FROM bell_events e JOIN bell_lists l ON l.id=e.list_id WHERE e.id=%s AND l.schedule_id=%s", (request.form.get("event_id"), scope_schedule_id))

def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ensure_bell_schema()
    demo = demo_mode_enabled()
    
    if request.path.rstrip("/") == "/bells/bell-lists":
        if demo and request.method == "POST":
            return bells_demo_return()
        if request.method == "POST":
            handle_list_post(0)
            return redirect("/bells/bell-lists")
        system_event_count = query_one(
            """
            SELECT COUNT(*) AS total
            FROM bell_events e
            JOIN bell_lists l ON l.id = e.list_id
            WHERE l.schedule_id = 0
            """
        ) or {}
        body = f"""
        """
        return bells_page("System Bell Lists", "", '<a class="btn secondary" href="/bells"><i class="fa-solid fa-arrow-left"></i> Back</a>', body, user)
        
    sid = int(request.values.get("schedule_id", "0") or 0)
    schedule = schedule_or_404(sid)
    if demo and request.method == "POST":
        return bells_demo_return()
    if request.method == "POST":
        handle_list_post(sid)
        return redirect("/bells/lists?" + urlencode({"schedule_id": sid}))
        
    counts = query_one(
        """
        SELECT
            (SELECT COUNT(*) FROM bell_lists WHERE schedule_id=%s) AS custom_lists,
            (SELECT COUNT(*) FROM bell_events e JOIN bell_lists l ON l.id=e.list_id WHERE l.schedule_id=%s) AS custom_bells
        """,
        (sid, sid),
    ) or {}
    
    body = f"""
    {schedule_settings_card(schedule, "bells")}
    {render_bells_ui(sid, "/bells/lists", {"schedule_id": sid}, demo)}
    """
    return bells_page("Bell Lists", schedule["name"], '<a class="btn secondary" href="/bells"><i class="fa-solid fa-arrow-left"></i> Back</a>', body, user)
