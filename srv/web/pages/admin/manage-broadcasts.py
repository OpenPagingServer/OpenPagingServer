from datetime import datetime, timedelta

from flask import Response, jsonify

from active_broadcast_store import (
    fetch_active_broadcast,
    list_active_broadcasts,
    mark_active_broadcast_delivery,
    request_active_broadcast_stop,
)
from group_features import fetch_group_rows, record_is_bell, record_is_immediate, record_is_livepage, selected_group_ids
from srv.web.app import *


BROADCASTS_STYLE = r"""
.header-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;flex-wrap:wrap;}
.page-header h1{margin:0;font-weight:400;color:#202124;}
.dropdown{position:relative;display:inline-block;}
.dropbtn{background:none;border:none;font-size:1.2em;cursor:pointer;color:#777;padding:5px 10px;}
.dropbtn:hover,.dropbtn:focus{background:transparent;color:#555;}
.dropdown-content{display:none;position:absolute;right:0;min-width:170px;background:#F9F9F9;border:1px solid #E0E0E0;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.16);z-index:10;padding:6px 0;}
.dropdown-content.open{display:block;}
.menu-action{display:flex;align-items:center;gap:8px;width:100%;border:none;background:transparent;color:#202124;padding:10px 14px;cursor:pointer;font:inherit;text-align:left;}
.menu-action:hover{background:#F1F3F4;}
.broadcast-table{border:1px solid #E0E0E0;border-radius:12px;background:#FFF;overflow:hidden;}
.broadcast-head,.broadcast-row{display:grid;grid-template-columns:minmax(240px,1.8fr) minmax(90px,.55fr) minmax(160px,1fr) minmax(110px,.75fr) minmax(145px,.85fr) auto;gap:12px;align-items:start;padding:10px 14px;}
.broadcast-head{background:#F8F9FA;color:#5F6368;font-size:0.78em;font-weight:600;letter-spacing:.03em;text-transform:uppercase;}
.broadcast-row{border-top:1px solid #ECEFF1;}
.broadcast-row.emergency{box-shadow:inset 4px 0 0 #C62828;}
.broadcast-row.live{box-shadow:inset 4px 0 0 #1976D2;}
.broadcast-primary{min-width:0;}
.broadcast-title{font-weight:500;color:#202124;line-height:1.28;overflow-wrap:anywhere;font-size:0.97em;}
.broadcast-subtitle{margin-top:2px;color:#5F6368;font-size:0.86em;line-height:1.34;white-space:pre-wrap;}
.broadcast-cell{min-width:0;color:#202124;line-height:1.32;overflow-wrap:anywhere;font-size:0.92em;}
.broadcast-cell.muted{color:#5F6368;}
.broadcast-actions{display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap;align-self:center;}
.btn-secondary,.btn-danger{display:inline-flex;align-items:center;justify-content:center;gap:6px;border-radius:9px;padding:7px 10px;font:inherit;font-size:0.9em;cursor:pointer;text-decoration:none;}
.btn-secondary{background:#FFF;color:#374151;border:1px solid #D1D5DB;}
.btn-danger{background:#C62828;color:#FFF;border:1px solid #C62828;}
.btn-secondary:hover{background:#F9FAFB;border-color:#C5CAD0;}
.btn-danger:hover{background:#B71C1C;border-color:#B71C1C;}
.empty-state{padding:22px 16px;color:#5F6368;}
.empty-state strong{display:block;margin-bottom:6px;color:#202124;font-weight:500;}
@media(max-width:1100px){
  .broadcast-head{display:none;}
  .broadcast-row{grid-template-columns:1fr;gap:10px;}
  .broadcast-cell[data-label]::before,.broadcast-actions[data-label]::before{
    content:attr(data-label);
    display:block;
    margin-bottom:4px;
    color:#5F6368;
    font-size:0.8em;
    font-weight:600;
    letter-spacing:.03em;
    text-transform:uppercase;
  }
  .broadcast-actions{justify-content:flex-start;}
}
@media(prefers-color-scheme:dark){
  .page-header h1,.broadcast-title,.broadcast-cell,.empty-state strong{color:#EDEDED;}
  .dropbtn{color:#BBB;}
  .dropbtn:hover,.dropbtn:focus{background:transparent;color:#E5E7EB;}
  .dropdown-content{background:#252525;border-color:#333;}
  .menu-action{color:#E5E7EB;}
  .menu-action:hover{background:#303030;}
  .broadcast-table{background:#1E1E1E;border-color:#333;}
  .broadcast-head{background:#202124;color:#BBB;}
  .broadcast-row{border-top-color:#333;background:#1E1E1E;}
  .broadcast-subtitle,.broadcast-cell.muted{color:#BBB;}
  .btn-secondary{background:#252525;color:#E5E7EB;border-color:#444;}
  .btn-secondary:hover{background:#303030;border-color:#555;}
  .btn-danger{background:#C62828;color:#FFF;border-color:#C62828;}
  .btn-danger:hover{background:#8E1E1E;border-color:#8E1E1E;}
  .empty-state{color:#BBB;}
}
"""

BROADCAST_STALE_SECONDS = 600
RECENT_FINISHED_SECONDS = 120


def parse_record_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def broadcast_is_stale(record, seconds=BROADCAST_STALE_SECONDS):
    issued = parse_record_datetime((record or {}).get("issued"))
    if issued is None:
        return False
    return issued < (datetime.now() - timedelta(seconds=max(1, int(seconds))))


def broadcast_is_recent(record, seconds=RECENT_FINISHED_SECONDS):
    issued = parse_record_datetime((record or {}).get("issued"))
    if issued is None:
        return False
    return issued >= (datetime.now() - timedelta(seconds=max(1, int(seconds))))


def delivery_is_broadcasting(record):
    delivery = str((record or {}).get("delivery") or "").strip().lower()
    if delivery in {"sent", "complete", "completed", "done"}:
        return False
    if broadcast_is_stale(record) and (delivery == "pending" or delivery.startswith("sending") or delivery == "live"):
        return False
    if record_is_livepage(record):
        return delivery in {"live", "pending"} or delivery.startswith("sending")
    return delivery == "pending" or delivery.startswith("sending")


def recently_finished_record(record, seconds=RECENT_FINISHED_SECONDS):
    delivery = str((record or {}).get("delivery") or "").strip().lower()
    if delivery_is_broadcasting(record):
        return False
    if record_is_livepage(record) or record_is_bell(record):
        return False
    if delivery == "stopped":
        return broadcast_is_recent(record, seconds)
    return False


def is_visible_record(record):
    if not record or bool(record.get("monitor_child")):
        return False
    delivery = str((record or {}).get("delivery") or "").strip().lower()
    if delivery in {"expired", "failed"}:
        return False
    broadcasting = delivery_is_broadcasting(record)
    if record_is_livepage(record):
        return broadcasting
    if record_is_bell(record):
        return broadcasting
    if record_is_immediate(record) and not broadcasting:
        return False
    if delivery == "cancelled":
        return False
    if delivery == "stopped":
        return recently_finished_record(record)
    return True


def delivery_state_label(record):
    delivery = str((record or {}).get("delivery") or "").strip().lower()
    broadcasting = delivery_is_broadcasting(record)
    if record_is_livepage(record):
        return ""
    if record_is_bell(record):
        return "Broadcasting" if broadcasting else "Stopped"
    if delivery == "stopped":
        return "Stopped"
    if broadcasting and (delivery == "pending" or delivery.startswith("sending")):
        return "Broadcasting"
    return "In effect"


def kind_label(record):
    if record_is_livepage(record):
        return "Page"
    if record_is_bell(record):
        return "Bell"
    type_value = str((record or {}).get("type") or "").strip()
    mapping = {
        "Page": "Message Page",
        "AudioMessage": "Audio Message",
        "TextMessage": "Visual Message",
        "Text+AudioMessage": "Audio + Visual Message",
    }
    return mapping.get(type_value, type_value or "Broadcast")


def priority_badge(record):
    priority = str((record or {}).get("priority") or "Normal").strip()
    if priority.lower() == "normal":
        return ""
    return priority


def title_label(record):
    if record_is_livepage(record):
        return "Page"
    return str(record.get("name") or kind_label(record) or "Broadcast").strip()


def sender_label(record):
    sender = str((record or {}).get("sender") or "").strip()
    if record_is_bell(record) and sender.lower() == "belld":
        return ""
    return sender or "System"


def groups_label(record, group_rows):
    if str((record or {}).get("groups") or "").strip() == "0":
        return ", ".join(row.get("name") or row.get("id") or "" for row in group_rows if row.get("name") or row.get("id")) or "All groups"
    wanted = [part.strip() for part in str((record or {}).get("groups") or "").split(".") if part.strip()]
    by_id = {str(row.get("id") or ""): row for row in group_rows}
    labels = []
    for group_id in wanted:
        row = by_id.get(group_id)
        labels.append((row or {}).get("name") or group_id)
    return ", ".join(labels) if labels else "No groups"


def can_stop_record(record):
    return delivery_is_broadcasting(record)


def can_expire_record(record):
    if record_is_livepage(record) or record_is_bell(record) or record_is_immediate(record):
        return False
    if delivery_is_broadcasting(record):
        return False
    tokens = [token.strip().lower() for token in str((record or {}).get("expires_rule") or "").split("|") if token.strip()]
    return "manual" in tokens or not tokens


def snapshots_with_children():
    snapshots = list_active_broadcasts(limit=500)
    by_parent = {}
    for record in snapshots:
        source_id = str(record.get("source_broadcast_id") or "").strip()
        if source_id:
            by_parent.setdefault(source_id, []).append(record)
    return snapshots, by_parent


def broadcast_sort_rank(record):
    if delivery_is_broadcasting(record):
        return 0
    if recently_finished_record(record):
        return 2
    return 1


def expirable_visible_broadcast_ids():
    snapshots, _by_parent = snapshots_with_children()
    visible = [record for record in snapshots if is_visible_record(record)]
    related = []
    for record in visible:
        if not can_expire_record(record):
            continue
        for token in cascade_related_ids(record.get("id")):
            if token not in related:
                related.append(token)
    return related


def record_visible_to_user(user, record, cursor):
    if not is_visible_record(record):
        return False
    allowed_groups = accessible_group_ids_for_user(user)
    if allowed_groups is not None:
        record_group_ids = {str(group_id or "").strip() for group_id in selected_group_ids((record or {}).get("groups"), cursor=cursor) if str(group_id or "").strip()}
        if not (record_group_ids & allowed_groups):
            return False
    template_id = str((record or {}).get("template_id") or "").strip()
    if template_id and not user_can_access_message(user, template_id):
        return False
    return True


def visible_records_for_user(user, cursor):
    snapshots, _by_parent = snapshots_with_children()
    visible = [record for record in snapshots if record_visible_to_user(user, record, cursor)]
    visible.sort(key=lambda item: parse_record_datetime(item.get("issued")) or datetime.min, reverse=True)
    visible.sort(key=broadcast_sort_rank)
    return visible


def render_broadcast_list(user):
    conn = db()
    try:
        with conn.cursor() as cur:
            group_rows = fetch_group_rows(cur)
            visible = visible_records_for_user(user, cur)
    finally:
        conn.close()
    group_rows = filter_group_rows_for_user(user, group_rows)
    manual_expirable_count = sum(1 for record in visible if can_expire_record(record))
    has_non_manual_visible = any(not can_expire_record(record) for record in visible)
    table_meta = (
        f'data-expire-all-count="{manual_expirable_count}" '
        f'data-expire-all-has-non-manual="{"1" if has_non_manual_visible else "0"}"'
    )
    if not visible:
        return f"""<div class="broadcast-table" {table_meta}>
<div class="broadcast-head">
    <div>Broadcast</div>
    <div>Type</div>
    <div>Groups</div>
    <div>Sender</div>
    <div>Started</div>
    <div>Actions</div>
</div>
<div class="empty-state">
    <strong>No active broadcasts.</strong>
</div></div>"""
    rows = [
        f"""<div class="broadcast-table" {table_meta}>
<div class="broadcast-head">
    <div>Broadcast</div>
    <div>Type</div>
    <div>Groups</div>
    <div>Sender</div>
    <div>Started</div>
    <div>Actions</div>
</div>"""
    ]
    for record in visible:
        title = title_label(record)
        can_expire = can_expire_record(record)
        row_classes = ["broadcast-row"]
        if record_is_livepage(record):
            row_classes.append("live")
        if str(record.get("priority") or "").strip().lower() == "emergency":
            row_classes.append("emergency")
        action_buttons = []
        if can_stop_record(record):
            action_buttons.append(
                f'<button type="button" class="btn-danger" onclick="performBroadcastAction(\'stop\', \'{h(record.get("id"))}\')"><i class="fa-solid fa-stop"></i> Stop</button>'
            )
        if can_expire:
            action_buttons.append(
                f'<button type="button" class="btn-secondary" onclick="performBroadcastAction(\'expire\', \'{h(record.get("id"))}\')"><i class="fa-solid fa-clock-rotate-left"></i> Expire</button>'
            )
        short_message = str(record.get("shortmessage") or "").strip()
        subtitle_parts = []
        state_text = delivery_state_label(record)
        priority_text = priority_badge(record)
        if state_text and not record_is_livepage(record):
            subtitle_parts.append(state_text)
        if priority_text:
            subtitle_parts.append(priority_text)
        if short_message and not record_is_livepage(record):
            subtitle_parts.append(short_message)
        subtitle = " | ".join(part for part in subtitle_parts if part)
        rows.append(
            f"""<div class="{' '.join(row_classes)}">
    <div class="broadcast-primary">
        <div class="broadcast-title">{h(title)}</div>
        {f'<div class="broadcast-subtitle">{h(subtitle)}</div>' if subtitle else ''}
    </div>
    <div class="broadcast-cell muted" data-label="Type">{h(kind_label(record))}</div>
    <div class="broadcast-cell" data-label="Groups">{h(groups_label(record, group_rows))}</div>
    <div class="broadcast-cell" data-label="Sender">{h(sender_label(record))}</div>
    <div class="broadcast-cell muted" data-label="Started">{h(record.get("issued") or "")}</div>
    <div class="broadcast-actions" data-label="Actions">
        {''.join(action_buttons)}
    </div>
</div>"""
        )
    rows.append("</div>")
    return "".join(rows)


def cascade_related_ids(broadcast_id):
    snapshots, by_parent = snapshots_with_children()
    related = [str(broadcast_id or "").strip()]
    for record in by_parent.get(str(broadcast_id or "").strip(), []):
        token = str(record.get("id") or "").strip()
        if token and token not in related:
            related.append(token)
    return [token for token in related if token]


def update_history_delivery(broadcast_ids, status):
    ids = [str(broadcast_id or "").strip() for broadcast_id in (broadcast_ids or []) if str(broadcast_id or "").strip()]
    if not ids:
        return
    for broadcast_id in ids:
        execute("UPDATE broadcasts SET delivery=%s WHERE id=%s", (status, broadcast_id))


def handle_request():
    user = require_user()
    if not isinstance(user, dict):
        return user
    if not (is_admin_user(user) or can_manage_broadcasts(user)):
        abort(403)
    ctx = legacy_user_context(user)
    if request.method == "POST":
        action = str(request.form.get("action") or "").strip().lower()
        broadcast_id = str(request.form.get("broadcast_id") or "").strip()
        if not action:
            return jsonify(ok=False, error="Missing action."), 400
        conn = db()
        try:
            with conn.cursor() as cur:
                visible_lookup = {
                    str(record.get("id") or "").strip(): record
                    for record in visible_records_for_user(user, cur)
                }
        finally:
            conn.close()
        if action == "expire_all":
            visible = list(visible_lookup.values())
            if not visible:
                return jsonify(ok=True, count=0)
            related_ids = []
            history_expire_ids = []
            for record in visible:
                if not can_expire_record(record):
                    continue
                record_related = cascade_related_ids(record.get("id"))
                for token in record_related:
                    if token not in related_ids:
                        related_ids.append(token)
                    if token not in history_expire_ids:
                        history_expire_ids.append(token)
            for token in related_ids:
                mark_active_broadcast_delivery(token, "expired")
            update_history_delivery(history_expire_ids, "expired")
            return jsonify(ok=True, count=len(history_expire_ids), expired=len(history_expire_ids))
        if not broadcast_id:
            return jsonify(ok=False, error="Missing broadcast_id."), 400
        if broadcast_id not in visible_lookup:
            return jsonify(ok=False, error="Broadcast not found."), 404
        related_ids = cascade_related_ids(broadcast_id)
        record = visible_lookup.get(broadcast_id) or fetch_active_broadcast(broadcast_id)
        if action == "stop":
            for token in related_ids:
                request_active_broadcast_stop(token)
            if record and (record_is_livepage(record) or record_is_bell(record)):
                for token in related_ids:
                    mark_active_broadcast_delivery(token, "stopped")
            return jsonify(ok=True)
        if action == "expire":
            if not record or not can_expire_record(record):
                return jsonify(ok=False, error="This broadcast cannot be expired right now."), 400
            for token in related_ids:
                mark_active_broadcast_delivery(token, "expired")
            update_history_delivery(related_ids, "expired")
            return jsonify(ok=True)
        return jsonify(ok=False, error="Unknown action."), 400

    if request.args.get("fragment") == "1":
        return Response(render_broadcast_list(user), mimetype="text/html")

    content = f"""<div class="page-header header-actions">
    <h1>Manage Broadcasts</h1>
    <div class="dropdown">
        <button type="button" class="dropbtn" onclick="event.stopPropagation(); toggleBroadcastMenu(this);" aria-label="Broadcast actions"><i class="fa-solid fa-ellipsis-vertical"></i></button>
        <div class="dropdown-content">
            <button type="button" class="menu-action" onclick="performBroadcastAction('expire_all')"><i class="fa-solid fa-clock-rotate-left"></i> Expire All</button>
        </div>
    </div>
</div>
<div id="broadcastListRoot">{render_broadcast_list(user)}</div>"""
    script = r"""
function toggleBroadcastMenu(button) {
  document.querySelectorAll('.dropdown-content.open').forEach(function(menu) {
    if (menu !== button.nextElementSibling) menu.classList.remove('open');
  });
  var menu = button && button.nextElementSibling;
  if (menu) menu.classList.toggle('open');
}
document.addEventListener('click', function(event) {
  document.querySelectorAll('.dropdown-content.open').forEach(function(menu) {
    if (!menu.contains(event.target) && !(menu.previousElementSibling && menu.previousElementSibling.contains(event.target))) {
      menu.classList.remove('open');
    }
  });
});
async function refreshBroadcasts() {
  try {
    const response = await fetch('/admin/manage-broadcasts?fragment=1', { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
    if (!response.ok) throw new Error('Request failed');
    document.getElementById('broadcastListRoot').innerHTML = await response.text();
  } catch (_error) {}
}
function expireAllPromptMessage() {
  var table = document.querySelector('#broadcastListRoot .broadcast-table');
  var lines = ['Expire all broadcasts?'];
  if (table && table.getAttribute('data-expire-all-has-non-manual') === '1') {
    lines.push('', 'Only manually expirable messages will be expired.');
  }
  return lines.join('\n');
}
async function performBroadcastAction(action, broadcastId) {
  try {
    document.querySelectorAll('.dropdown-content.open').forEach(function(menu) { menu.classList.remove('open'); });
    if (action === 'expire_all' && !window.confirm(expireAllPromptMessage())) {
      return;
    }
    const params = new URLSearchParams({ action: action });
    if (broadcastId) params.set('broadcast_id', broadcastId);
    const response = await fetch('/admin/manage-broadcasts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' },
      body: params
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      alert(payload && payload.error ? payload.error : 'Action failed.');
      return;
    }
    await refreshBroadcasts();
  } catch (_error) {
    alert('Action failed.');
  }
}
refreshBroadcasts();
setInterval(refreshBroadcasts, 2000);
"""
    return legacy_page("Manage Broadcasts", ctx, "broadcasts", BROADCASTS_STYLE, content, script)
