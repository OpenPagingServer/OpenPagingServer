from srv.web.app import *
from active_broadcast_store import RUNTIME_DIR, fetch_active_broadcast

SEND_STATUS_KEY_RE = re.compile(r"^[A-Fa-f0-9]{16,64}$")

SEND_STATUS_STYLE = r"""
.status-wrap{min-height:calc(100vh - 100px);display:flex;align-items:center;justify-content:center;}
.status-card{width:100%;max-width:520px;background:transparent;border:none;padding:40px 32px;text-align:center;}
.status-icon{width:72px;height:72px;margin:0 auto 20px auto;display:block;}
.status-title{font-size:1.25em;font-weight:500;margin:0 0 8px 0;color:#212121;}
.status-detail{color:#5F6368;font-size:0.95em;margin:0 0 24px 0;line-height:1.5;}
.md-spinner{animation:md-rotate 1.4s linear infinite;}
.md-spinner circle{stroke:#1976D2;stroke-dasharray:187;stroke-dashoffset:46.75;stroke-linecap:round;transform-origin:center;animation:md-dash 1.4s ease-in-out infinite;}
@keyframes md-rotate{100%{transform:rotate(360deg);}}
@keyframes md-dash{0%{stroke-dashoffset:187;}50%{stroke-dashoffset:46.75;transform:rotate(135deg);}100%{stroke-dashoffset:187;transform:rotate(450deg);}}
.status-actions{display:flex;align-items:center;justify-content:center;gap:12px;flex-wrap:wrap;}
.btn-return,.btn-debug{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:44px;box-sizing:border-box;color:#FFF;border:none;padding:0 20px;border-radius:24px;font-size:14px;font-weight:500;line-height:1;cursor:pointer;text-decoration:none;box-shadow:0 1px 3px rgba(0,0,0,0.2);}
.btn-return{background:#1976D2;}
.btn-return:hover{background:#1565C0;}
.btn-return svg{width:20px;height:20px;fill:#FFF;flex:0 0 auto;}
.btn-return span{display:inline-flex;align-items:center;}
.btn-debug{background:#616161;}
.btn-debug:hover{background:#424242;}
@media(prefers-color-scheme:dark){
.status-title{color:#E0E0E0;}
.status-detail{color:#9E9E9E;}
.md-spinner circle{stroke:#8AB4F8;}
.btn-return{background:#8AB4F8;color:#000;}
.btn-return:hover{background:#AECBFA;}
.btn-return svg{fill:#000;}
}
"""

ICON_SPINNER = '<svg class="status-icon md-spinner" viewBox="0 0 66 66"><circle fill="none" stroke-width="6" cx="33" cy="33" r="30"></circle></svg>'
ICON_SUCCESS = '<svg class="status-icon" viewBox="0 0 24 24"><path fill="#2E7D32" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>'
ICON_WARNING = '<svg class="status-icon" viewBox="0 0 24 24"><path fill="#F9A825" d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>'
ICON_ERROR = '<svg class="status-icon" viewBox="0 0 24 24"><path fill="#C62828" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>'
ICON_RETURN = '<svg viewBox="0 0 24 24"><path d="M9 14l-4-4 4-4v3h7a4 4 0 0 1 4 4v5h-2v-5a2 2 0 0 0-2-2H9v3z"/></svg>'


def send_status_debug_path(key):
    return RUNTIME_DIR / f"send-debug-{str(key).lower()}.log"


def append_send_status_debug(key, text):
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with open(send_status_debug_path(key), "a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except OSError:
        pass


def broadcast_delivery_record(broadcast_id):
    record = fetch_active_broadcast(broadcast_id)
    if record:
        return record
    row = query_one("SELECT id, delivery, groups FROM broadcasts WHERE id=%s LIMIT 1", (broadcast_id,))
    return row


def broadcast_recipient_report(record):
    report = {"unavailable": [], "endpoint_error": ""}
    endpoint_data = endpoint_ipc("LIST_ENDPOINTS")
    if not endpoint_data.get("ok", True):
        report["endpoint_error"] = str(endpoint_data.get("error") or "Endpoint manager returned an error.")
        return report
    availability = endpoint_availability_map(endpoint_data)
    group_ids = [part.strip() for part in str(record.get("groups") or "").split(".") if part.strip()]
    if not group_ids:
        return report
    if "0" in group_ids:
        rows = query_all("SELECT id, name, members FROM `groups`")
    else:
        placeholders = ",".join(["%s"] * len(group_ids))
        rows = query_all(f"SELECT id, name, members FROM `groups` WHERE id IN ({placeholders})", tuple(group_ids))
    seen = set()
    for row in rows:
        for member in group_member_tokens(row.get("members")):
            if member in seen:
                continue
            seen.add(member)
            if member == GUEST_MEMBER_TOKEN or is_desktop_member_token(member):
                continue
            if not group_member_available(member, availability):
                report["unavailable"].append(member)
    return report


def send_status_state(broadcast_id):
    record = broadcast_delivery_record(broadcast_id)
    if not record:
        return {"state": "failed", "detail": "The broadcast could not be found."}
    delivery = str(record.get("delivery") or "pending").lower()
    if delivery == "pending":
        return {"state": "sending", "detail": ""}
    if delivery == "failed":
        append_send_status_debug(broadcast_id, f"delivery=failed record={json.dumps({k: str(v) for k, v in record.items() if k != 'payload'})}")
        return {"state": "failed", "detail": "The broadcast could not be delivered to any recipient."}
    report = broadcast_recipient_report(record)
    if report["endpoint_error"] or report["unavailable"]:
        append_send_status_debug(
            broadcast_id,
            f"delivery={delivery} endpoint_error={report['endpoint_error']!r} unavailable_recipients={report['unavailable']}",
        )
        return {"state": "partial", "detail": ""}
    return {"state": "sent", "detail": ""}


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    if not can_send_messages(user):
        abort(403)
    bid = str(request.args.get("bid") or "").strip()
    fail_key = str(request.args.get("fail") or "").strip()
    key = bid or fail_key
    if not key or not SEND_STATUS_KEY_RE.fullmatch(key):
        return redirect("/messages/")
    if request.args.get("debug") == "1":
        if not APP_DEBUG:
            abort(404)
        path = send_status_debug_path(key)
        text = ""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = "No debug log is available for this broadcast."
        return Response(text, mimetype="text/plain")
    if request.args.get("poll") == "1":
        if fail_key and not bid:
            return jsonify({"state": "failed", "debug": APP_DEBUG})
        state = send_status_state(bid)
        state["debug"] = APP_DEBUG
        return jsonify(state)
    ctx = legacy_user_context(user)
    debug_url = f"/messages/send-status?{'bid=' + h(bid) if bid else 'fail=' + h(fail_key)}&debug=1"
    initial_failed = "true" if (fail_key and not bid) else "false"
    content = f"""    <div class="status-wrap">
    <div class="status-card">
        <div id="status-icon">{ICON_SPINNER}</div>
        <p class="status-title" id="status-title">Sending broadcast...</p>
        <p class="status-detail" id="status-detail"></p>
        <div class="status-actions" id="status-actions" style="display:none;">
            <a href="/messages/" class="btn-return" id="return-button">{ICON_RETURN} Return <span id="return-countdown">(15)</span></a>
            <a href="{debug_url}" target="_blank" class="btn-debug" id="debug-button" style="display:none;">Show Debug</a>
        </div>
    </div>
    </div>"""
    script = f"""
const ICONS = {{
    success: {json.dumps(ICON_SUCCESS)},
    partial: {json.dumps(ICON_WARNING)},
    failed: {json.dumps(ICON_ERROR)},
}};
const POLL_URL = "/messages/send-status?{('bid=' + h(bid)) if bid else ('fail=' + h(fail_key))}&poll=1";
const INITIAL_FAILED = {initial_failed};
let finished = false;
let polls = 0;
function finish(state, showDebug) {{
    if (finished) return;
    finished = true;
    const icon = document.getElementById('status-icon');
    const title = document.getElementById('status-title');
    const detail = document.getElementById('status-detail');
    if (state === 'sent') {{
        icon.innerHTML = ICONS.success;
        title.textContent = 'Broadcast sent';
    }} else if (state === 'partial') {{
        icon.innerHTML = ICONS.partial;
        title.textContent = 'Errors sending broadcast';
        detail.textContent = 'Some recipients may not have received it. Contact your system administrator for help.';
    }} else {{
        icon.innerHTML = ICONS.failed;
        title.textContent = 'Failed to send broadcast';
        detail.textContent = 'Contact your system administrator for help.';
    }}
    document.getElementById('status-actions').style.display = 'flex';
    if (showDebug && state !== 'sent') document.getElementById('debug-button').style.display = 'inline-flex';
    let remaining = 15;
    const countdown = document.getElementById('return-countdown');
    const timer = setInterval(function() {{
        remaining -= 1;
        countdown.textContent = '(' + remaining + ')';
        if (remaining <= 0) {{
            clearInterval(timer);
            window.location.href = '/messages/';
        }}
    }}, 1000);
}}
function poll() {{
    if (finished) return;
    polls += 1;
    fetch(POLL_URL, {{credentials: 'same-origin'}})
        .then(function(resp) {{ return resp.json(); }})
        .then(function(data) {{
            if (data.state === 'sending') {{
                if (polls >= 90) {{ finish('failed', data.debug); return; }}
                setTimeout(poll, 1000);
                return;
            }}
            finish(data.state === 'sent' ? 'sent' : (data.state === 'partial' ? 'partial' : 'failed'), data.debug);
        }})
        .catch(function() {{
            if (polls >= 90) {{ finish('failed', false); return; }}
            setTimeout(poll, 1500);
        }});
}}
if (INITIAL_FAILED) {{
    fetch(POLL_URL, {{credentials: 'same-origin'}})
        .then(function(resp) {{ return resp.json(); }})
        .then(function(data) {{ finish('failed', data.debug); }})
        .catch(function() {{ finish('failed', false); }});
}} else {{
    setTimeout(poll, 700);
}}
"""
    return legacy_page("Sending Broadcast", ctx, "messages", SEND_STATUS_STYLE, content, script)
