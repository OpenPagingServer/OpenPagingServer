from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    if request.method == "POST":
        if demo_mode_enabled():
            return jsonify(status="error", message="Demo Mode is enabled.") if request.headers.get("X-Requested-With") == "XMLHttpRequest" else demo_mode_page("General Settings", legacy_user_context(user), "settings", "settings")
        show_docs = "1" if request.form.get("show_online_docs") else "0"
        analytics = "1" if request.form.get("analytics") else "0"
        allow_multicast_gateway = "1" if request.form.get("allow_multicast_gateway") else "0"
        save_setting("show_online_docs", show_docs, "Show GUI links to docs.openpagingserver.org (0/1)")
        save_setting("analytics", analytics, "To help the Open Paging Server project improve, you can opt-in to share optional analytics.")
        save_setting("allow_multicast_gateway", allow_multicast_gateway, "Allow Multicast Gateway connections to this server (0/1)")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/general")
    ctx = legacy_user_context(user)
    show_docs_checked = " checked" if data.get("show_online_docs", "1") == "1" else ""
    analytics_checked = " checked" if data.get("analytics", "0") == "1" else ""
    allow_multicast_gateway_checked = " checked" if data.get("allow_multicast_gateway", "0") == "1" else ""
    demo = demo_mode_enabled()
    docs_link = "https://www.openpagingserver.org/software/multicastgateway/"
    gateway_name = f'<a href="{docs_link}" target="_blank" rel="noopener">Multicast Gateway</a>' if data.get("show_online_docs", "1") == "1" else "Multicast Gateway"
    manage_servers_style = (
        "display:inline-flex; align-items:center; justify-content:center; height:36px; padding:0 18px; border:none; "
        "border-radius:999px; background:#1976D2; color:#FFF; text-decoration:none; font-weight:500; cursor:pointer; "
        "box-shadow:0 1px 3px rgba(0,0,0,0.2), 0 1px 2px rgba(0,0,0,0.12); white-space:nowrap;"
        if data.get("allow_multicast_gateway", "0") == "1"
        else "display:none;"
    )
    body = f"""
    <div id="general" class="tab-content active">
        <style>
        .general-toggle-actions {{ display:inline-flex; align-items:center; gap:10px; flex-wrap:nowrap; margin-left:12px; }}
        .mg-modal-backdrop, .mg-nested-backdrop {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,0.55); z-index:2200; align-items:center; justify-content:center; padding:20px; box-sizing:border-box; }}
        .mg-modal-backdrop.active, .mg-nested-backdrop.active {{ display:flex; }}
        .mg-modal {{ width:min(680px, 100%); background:#FFF; border-radius:18px; box-shadow:0 24px 60px rgba(0,0,0,0.25); }}
        .mg-modal.small {{ width:min(520px, 100%); }}
        .mg-modal-header {{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:22px 22px 0 22px; }}
        .mg-modal-header h3 {{ margin:0; font-weight:500; color:#1976D2; }}
        .mg-modal-body {{ padding:18px 22px 22px 22px; }}
        .mg-modal-actions {{ display:flex; justify-content:flex-end; gap:10px; padding:0 22px 22px 22px; flex-wrap:wrap; }}
        .mg-icon-button {{ border:none; background:transparent; width:36px; height:36px; border-radius:50%; cursor:pointer; color:#5F6368; font-size:1.35em; }}
        .mg-icon-button:hover {{ background:#F1F3F4; }}
        .mg-flash {{ margin:0 0 14px 0; padding:12px 14px; border-radius:10px; border:1px solid; }}
        .mg-flash.success {{ background:#E8F5E9; border-color:#A5D6A7; color:#1B5E20; }}
        .mg-flash.error {{ background:#FFEBEE; border-color:#EF9A9A; color:#B71C1C; }}
        .mg-section-label {{ margin:0 0 8px 0; font-size:0.95em; font-weight:500; color:#555; }}
        .mg-public-key {{ border:1px solid #CCC; border-radius:8px; padding:12px; font-family:monospace; word-break:break-all; background:#F8FAFC; margin-bottom:18px; }}
        .mg-toolbar {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:14px; }}
        .mg-peer-list {{ display:grid; gap:12px; }}
        .mg-peer-item {{ border:1px solid #E5E7EB; border-radius:12px; padding:14px; background:#FAFBFD; }}
        .mg-peer-head {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:8px; flex-wrap:wrap; }}
        .mg-peer-name {{ font-weight:500; color:#202124; }}
        .mg-peer-meta {{ color:#666; font-size:0.92em; margin-bottom:10px; }}
        .mg-peer-key-label {{ color:#666; font-size:0.84em; margin-bottom:6px; }}
        .mg-peer-key {{ border:1px solid #CCC; border-radius:8px; padding:12px; font-family:monospace; background:#FFF; word-break:break-all; }}
        .mg-empty-state {{ border:1px dashed #D0D7E2; border-radius:12px; padding:16px; color:#666; background:#FAFBFD; text-align:center; }}
        .mg-field {{ margin-bottom:14px; }}
        .mg-field label {{ display:block; margin-bottom:6px; color:#555; font-weight:500; }}
        .mg-field input {{ width:100%; padding:12px; border-radius:8px; border:1px solid #CCC; font-family:inherit; font-size:14px; box-sizing:border-box; background:#FFF; }}
        .mg-field-note {{ color:#666; font-size:0.9em; line-height:1.4; }}
        .mg-text-button, .mg-filled-button {{ border:none; border-radius:999px; padding:10px 16px; font-size:14px; cursor:pointer; }}
        .mg-text-button {{ background:#E8EEF5; color:#202124; }}
        .mg-text-button.danger {{ background:#FDECEC; color:#B71C1C; }}
        .mg-filled-button {{ background:#1976D2; color:#FFF; }}
        .mg-fab {{ width:40px; height:40px; border:none; border-radius:50%; background:#1976D2; color:#FFF; font-size:1.4em; line-height:1; cursor:pointer; }}
        @media (max-width:767px) {{
            .general-toggle-actions {{ margin-left:0; }}
            .mg-modal-header {{ padding:18px 18px 0 18px; }}
            .mg-modal-body {{ padding:16px 18px 18px 18px; }}
            .mg-modal-actions {{ padding:0 18px 18px 18px; }}
        }}
        @media(prefers-color-scheme:dark) {{
            .mg-modal, .mg-peer-item {{ background:#1E1E1E; }}
            .mg-modal-header h3 {{ color:#BB86FC; }}
            .mg-icon-button {{ color:#BBB; }}
            .mg-icon-button:hover {{ background:#333; }}
            .mg-section-label, .mg-peer-meta, .mg-peer-key-label, .mg-field label, .mg-field-note, .mg-empty-state {{ color:#BBB; }}
            .mg-peer-name, .mg-public-key, .mg-peer-key {{ color:#EDEDED; }}
            .mg-public-key, .mg-peer-key, .mg-field input {{ background:#121212; border-color:#444; color:#E0E0E0; }}
            .mg-peer-item, .mg-empty-state {{ border-color:#333; background:#171A1F; }}
            .mg-text-button {{ background:#333; color:#E0E0E0; }}
            .mg-text-button.danger {{ background:#4C1D1D; color:#FCA5A5; }}
            .mg-filled-button, .mg-fab {{ background:#BB86FC; color:#000; }}
        }}
        </style>
        <div class="info-card login-settings">
            <form id="generalSettingsForm">
                <div class="info-row">
                    <span class="info-label">Show links to online documentation (docs.openpagingserver.org)</span>
                    <span><label class="switch"><input type="checkbox" name="show_online_docs" id="docsToggle"{show_docs_checked}><span class="slider"></span></label></span>
                </div>
                <div class="info-row">
                    <span class="info-label">
                        Send optional analytics to the Open Paging Server project
                        <span class="info-description">To help the Open Paging Server project improve, you can opt-in to share optional analytics. Analytics contain mainly anonymous data such as your operating system, software versions, anonymized crash logs, etc. And may include your public IP address. <a href="https://www.openpagingserver.org/privacypolicy/analytics" target="_blank" rel="noopener">Privacy Policy</a></span>
                    </span>
                    <span><label class="switch"><input type="checkbox" name="analytics" id="analyticsToggle"{analytics_checked}><span class="slider"></span></label></span>
                </div>
                <div class="info-row">
                    <span class="info-label">
                        Allow connection to Multicast Gateway
                        <span class="info-description">When enabled, {gateway_name} can be connected to this server to allow multicast packets to travel between WAN and VPN tunnels. UDP port 8710 must be accessible from Multicast Gateway to {h(ctx.get("product_name") or "Open Paging Server")}, as well as the HTTP on this server for automatic provisioning.</span>
                    </span>
                    <span class="general-toggle-actions">
                        <button type="button" id="manageMulticastGatewayBtn" style="{manage_servers_style}">Manage Servers</button>
                        <label class="switch"><input type="checkbox" name="allow_multicast_gateway" id="multicastGatewayToggle"{allow_multicast_gateway_checked}><span class="slider"></span></label>
                    </span>
                </div>
                <input type="hidden" name="save_general_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveGeneralBtn">Save Settings</button>
                    <span id="general-save-status" class="save-status"></span>
                </div>
            </form>
        </div>
        <div id="mgManagerBackdrop" class="mg-modal-backdrop">
            <div id="mgManagerRoot"></div>
        </div>
    </div>"""
    script = """
document.addEventListener('DOMContentLoaded', function(){
  postSettings('generalSettingsForm','saveGeneralBtn','general-save-status','General settings saved.', false);
  var manageButton = document.getElementById('manageMulticastGatewayBtn');
  var managerBackdrop = document.getElementById('mgManagerBackdrop');
  var managerRoot = document.getElementById('mgManagerRoot');
  var demoModeEnabled = __OPS_DEMO_MODE__;
  function closeMainModal() {
    if (managerBackdrop) managerBackdrop.classList.remove('active');
    if (managerRoot) managerRoot.innerHTML = '';
  }
  function closeAddModal() {
    var addOverlay = document.getElementById('mgAddServerOverlay');
    if (addOverlay) addOverlay.classList.remove('active');
  }
  function openAddModal() {
    var addOverlay = document.getElementById('mgAddServerOverlay');
    if (addOverlay) addOverlay.classList.add('active');
  }
  function refreshManager(message) {
    var url = '/admin/settings/multicast-gateway?fragment=1';
    if (message) url += '&_=' + encodeURIComponent(String(Date.now()));
    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function(response) { return response.text(); })
      .then(function(html) {
        managerRoot.innerHTML = html;
        managerBackdrop.classList.add('active');
      })
      .catch(function() {
        managerRoot.innerHTML = '<div class="mg-modal-panel"><div class="mg-modal-body"><div class="mg-status-banner" style="border-color:#EF9A9A; background:#FFEBEE; color:#B71C1C;">Unable to load Multicast Gateway servers.</div></div></div>';
        managerBackdrop.classList.add('active');
      });
  }
  function submitManagerForm(formData) {
    fetch('/admin/settings/multicast-gateway?fragment=1', {
      method: 'POST',
      body: formData,
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(function(response) { return response.json(); })
    .then(function(data) {
      if (data && typeof data.html === 'string') {
        managerRoot.innerHTML = data.html;
        managerBackdrop.classList.add('active');
      }
      if (data && data.status === 'success') closeAddModal();
    })
    .catch(function() {
      alert('Unable to save Multicast Gateway server settings.');
    });
  }
  if (manageButton) {
    manageButton.addEventListener('click', function() {
      if (demoModeEnabled) {
        if (window.openDemoModePopup) openDemoModePopup('settings');
        return;
      }
      refreshManager();
    });
  }
  if (managerBackdrop) {
    managerBackdrop.addEventListener('click', function(event) {
      if (event.target === managerBackdrop) closeMainModal();
      if (event.target && event.target.id === 'mgAddServerOverlay') closeAddModal();
    });
  }
  if (managerRoot) {
    managerRoot.addEventListener('click', function(event) {
      var closeTarget = event.target.closest('[data-mg-close]');
      if (closeTarget) {
        if (closeTarget.getAttribute('data-mg-close') === 'main') closeMainModal();
        if (closeTarget.getAttribute('data-mg-close') === 'add') closeAddModal();
        return;
      }
      var addTarget = event.target.closest('[data-mg-open-add]');
      if (addTarget) {
        openAddModal();
        return;
      }
      var removeTarget = event.target.closest('.mg-remove-btn');
      if (removeTarget) {
        if (!confirm('Remove this server?')) return;
        var formData = new FormData();
        formData.append('action', 'delete');
        formData.append('peer_id', removeTarget.getAttribute('data-peer-id') || '');
        submitManagerForm(formData);
      }
    });
    managerRoot.addEventListener('submit', function(event) {
      var form = event.target;
      if (!form || form.id !== 'mgAddServerForm') return;
      event.preventDefault();
      submitManagerForm(new FormData(form));
    });
  }
});
""".replace("__OPS_DEMO_MODE__", "true" if demo else "false")
    return settings_page("General Settings", ctx, "general", body, script)
