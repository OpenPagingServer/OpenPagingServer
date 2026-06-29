from srv.web.app import *


def _unauthorized_response():
    return jsonify(status="error", message="Unauthorized."), 401


def handle_request():
    if not demo_mode_maintenance_session_active():
        return _unauthorized_response()

    state = demo_mode_maintenance_state()
    if request.method != "POST":
        return jsonify(status="success", state=state)

    action = str(request.form.get("action") or "").strip().lower()
    try:
        if action == "save":
            block_value = "1" if truthy(request.form.get("block_non_maintenance_users")) else "0"
            save_setting(
                DEMO_MODE_MAINTENANCE_BLOCK_SETTING,
                block_value,
                "When demo mode maintenance is active, only the maintenance user may access the web interface. (0/1)",
            )
            state = demo_mode_maintenance_state()
            return jsonify(status="success", message="Saved.", state=state)
        if action == "enter_web":
            session[DEMO_MODE_MAINTENANCE_PENDING_KEY] = "0"
            demo_mode_maintenance_touch()
            return jsonify(status="success", message="Entering web interface.", redirect="/dashboard", state=state)
        if action == "restart_ops_systemd":
            demo_mode_maintenance_restart_ops_systemd()
            return jsonify(status="success", message="OPS systemd restart requested.", state=state)
        if action == "reboot_server":
            demo_mode_maintenance_reboot_server()
            return jsonify(status="success", message="Server reboot requested.", state=state)
        return jsonify(status="error", message="Unknown action.", state=state), 400
    except Exception as exc:
        return jsonify(status="error", message=str(exc), state=state), 400
