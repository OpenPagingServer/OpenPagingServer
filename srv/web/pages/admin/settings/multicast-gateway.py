import time

from srv.web.app import *

from multicastgatewayd import DEFAULT_PORT, delete_ops_peer, ensure_ops_identity, ensure_peer_table, list_ops_peers, upsert_ops_peer
from multicastgatewayd import update_ops_peer_presence, peer_status_from_timestamp


def message_html(message, kind):
    if not message:
        return ""
    ok = str(kind or "").strip().lower() == "success"
    css = "success" if ok else "error"
    return f'<div class="mg-flash {css}">{h(message)}</div>'


def status_text(row, now_value):
    state = peer_status_from_timestamp(row.get("last_seen"), now=now_value)
    last_ip = str(row.get("last_ip") or "").strip()
    if state == "Online" and last_ip:
        return f"Online ({last_ip})"
    return "Offline"


def server_cards_html():
    rows = list_ops_peers(query_all)
    now_value = time.time()
    rendered = []
    for row in rows:
        rendered.append(
            f"""
            <div class="mg-peer-item">
                <div class="mg-peer-head">
                    <div class="mg-peer-name">{h(row.get("label") or "(unlabeled)")}</div>
                    <button type="button" class="mg-text-button danger" data-peer-id="{h(row.get("id"))}">Remove</button>
                </div>
                <div class="mg-peer-meta">Status: {h(status_text(row, now_value))}</div>
                <div class="mg-peer-key-label">Public Key</div>
                <div class="mg-peer-key">{h(str(row.get("public_key") or ""))}</div>
            </div>
            """
        )
    if rendered:
        return "".join(rendered)
    return '<div class="mg-empty-state">No provisioned servers.</div>'


def popup_content_html(identity, message="", kind="success"):
    return f"""
    <div class="mg-modal" role="dialog" aria-modal="true" aria-labelledby="mgModalTitle">
        <div class="mg-modal-header">
            <h3 id="mgModalTitle">Manage Servers</h3>
            <button type="button" class="mg-icon-button" data-mg-close="main" aria-label="Close">&times;</button>
        </div>
        <div class="mg-modal-body">
            {message_html(message, kind)}
            <div class="mg-section-label">Server Public Key</div>
            <div class="mg-public-key">{h(identity["public_key"])}</div>
            <div class="mg-toolbar">
                <div class="mg-section-label" style="margin:0;">Servers</div>
                <button type="button" class="mg-fab" data-mg-open-add="1" aria-label="Add server">+</button>
            </div>
            <div class="mg-peer-list">{server_cards_html()}</div>
        </div>
    </div>
    <div id="mgAddServerOverlay" class="mg-nested-backdrop">
        <div class="mg-modal small" role="dialog" aria-modal="true" aria-labelledby="mgAddServerTitle">
            <div class="mg-modal-header">
                <h3 id="mgAddServerTitle">Add Server</h3>
                <button type="button" class="mg-icon-button" data-mg-close="add" aria-label="Close">&times;</button>
            </div>
            <form id="mgAddServerForm">
                <div class="mg-modal-body">
                    <input type="hidden" name="action" value="add">
                    <div class="mg-field">
                        <label for="mgLabel">Label</label>
                        <input id="mgLabel" type="text" name="label" maxlength="255" placeholder="Server label">
                    </div>
                    <div class="mg-field">
                        <label for="mgPublicKey">Public Key</label>
                        <input id="mgPublicKey" type="text" name="public_key" maxlength="128" placeholder="Peer public key" required>
                    </div>
                </div>
                <div class="mg-modal-actions">
                    <button type="button" class="mg-text-button" data-mg-close="add">Cancel</button>
                    <button type="submit" class="mg-filled-button">Save</button>
                </div>
            </form>
        </div>
    </div>
    """


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    wants_fragment = request.args.get("fragment") == "1" or request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if demo_mode_enabled():
        if wants_fragment:
            return demo_mode_iframe_html("settings")
        return redirect("/admin/settings/general")

    ensure_peer_table()
    identity = ensure_ops_identity()

    if request.method == "POST":
        action = str(request.form.get("action") or "").strip().lower()
        status = "success"
        message = ""
        try:
            if action == "add":
                label = str(request.form.get("label") or "").strip()
                public_key = str(request.form.get("public_key") or "").strip()
                upsert_ops_peer(execute, label or public_key, "", DEFAULT_PORT, public_key, peer_type="gateway", enabled=1)
                message = "Server saved."
            elif action == "delete":
                delete_ops_peer(execute, request.form.get("peer_id") or 0)
                message = "Server removed."
            elif action == "presence":
                public_key = str(request.form.get("public_key") or "").strip()
                last_ip = str(request.form.get("last_ip") or "").strip()
                update_ops_peer_presence(execute, public_key, last_ip)
                message = "Server updated."
            else:
                raise ValueError("Unknown action.")
        except Exception as exc:
            status = "error"
            message = str(exc)
        if wants_fragment:
            return jsonify(status=status, message=message, html=popup_content_html(identity, message, status))
        return redirect("/admin/settings/general")

    if wants_fragment:
        return popup_content_html(identity)
    return redirect("/admin/settings/general")
