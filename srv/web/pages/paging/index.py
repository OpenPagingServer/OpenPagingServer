from srv.web.app import *
from group_features import fetch_group_rows

PAGING_STYLE = r"""
body, html { margin:0; padding:0; font-family:"Tahoma",sans-serif; font-weight:300; background-color:#FFF; height:100%; }
#sidebar { width:220px; background-color:#1976D2; color:#FFF; height:100vh; position:fixed; top:0; left:0; display:flex; flex-direction:column; box-shadow:2px 0 8px rgba(0,0,0,0.2); transition:transform 0.3s ease; z-index:1200; }
@media (max-width:767px){ #sidebar{ transform:translateX(-100%); } #sidebar.open{ transform:translateX(0); } }
#sidebar h2 { text-align:center; padding:20px 0; margin:0; font-weight:500; background-color:#1565C0; font-size:1.2em; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#FFF; padding:12px 20px; display:block; border-bottom:1px solid rgba(255,255,255,0.1); text-decoration:none; transition:background 0.3s; font-size:0.9em; text-align:left; box-sizing:border-box; }
#sidebar a i,.logout-btn i,.logout-btn-mobile i,.admin-only i { margin-right:8px; width:20px; }
#sidebar a:hover,#sidebar a.active{ background-color:#1565C0; }
.logout-btn{ background-color:#C62828; border:none; cursor:pointer; margin-top:auto; transition:background-color 0.3s; }
.logout-btn-mobile{ background-color:#C62828; border:none; cursor:pointer; transition:background-color 0.3s; display:none; }
@media(max-width:767px){ .logout-btn{ display:none; } .logout-btn-mobile{ display:block; } }
#mobile-header{ display:none; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
@media(max-width:767px){ #mobile-header{ display:flex; } }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; min-height:100vh; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; margin:0 0 18px; }
.layout { display:grid; grid-template-columns:minmax(260px, 1fr) minmax(280px, 360px); gap:18px; align-items:start; }
.layout.hidden { display:none; }
@media(max-width:900px){ .layout{ grid-template-columns:1fr; } }
.card{ background:#FFF; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); padding:16px; }
.card h2{ margin:0 0 14px; font-size:1.1em; font-weight:500; color:#1976D2; }
.info-row{ display:flex; justify-content:space-between; gap:12px; padding:10px 0; border-bottom:1px solid #f0f0f0; align-items:center; }
.info-row:last-child{ border-bottom:none; }
.field{ display:flex; flex-direction:column; gap:6px; margin-bottom:14px; }
.field label{ color:#555; font-size:0.9em; font-weight:500; }
.field select{ border:1px solid #CCC; border-radius:4px; padding:10px; font:inherit; background:#FFF; color:#202124; width:100%; }
.tips-card{ margin-top:18px; }
.tips-card p{ color:#555; line-height:1.5; margin:0 0 12px; }
.tips-card p:last-child{ margin-bottom:0; }
.recipient-note{ color:#777; font-size:0.9em; margin-left:auto; white-space:nowrap; }
.recipient-row.unavailable{ opacity:0.58; }
.recipient-row.unavailable .md-checkbox-container{ cursor:not-allowed; }
.recipient-row.unavailable .md-checkmark{ border-color:#BDBDBD; background:#F5F5F5; }
.md-checkbox-container{ display:flex; align-items:center; position:relative; cursor:pointer; font-size:14px; font-weight:500; color:#555; user-select:none; width:100%; padding:5px 0; gap:12px; }
.md-checkbox-container input{ position:absolute; opacity:0; cursor:pointer; height:0; width:0; }
.md-checkmark{ position:relative; display:inline-block; flex:0 0 auto; height:20px; width:20px; background-color:#fff; border:2px solid #5f6368; border-radius:2px; transition:all 0.2s; }
.md-checkbox-container:hover input ~ .md-checkmark{ border-color:#202124; }
.md-checkbox-container input:checked ~ .md-checkmark{ background-color:#1976D2; border-color:#1976D2; }
.md-checkmark:after{ content:""; position:absolute; display:none; left:6px; top:2px; width:4px; height:10px; border:solid white; border-width:0 2px 2px 0; transform:rotate(45deg); }
.md-checkbox-container input:checked ~ .md-checkmark:after{ display:block; }
.page-control{ display:flex; flex-direction:column; align-items:center; gap:14px; padding:10px 0; }
.mic-button{ width:132px; height:132px; border-radius:50%; border:none; background:#1976D2; color:#FFF; cursor:pointer; box-shadow:0 8px 18px rgba(25,118,210,0.28); transition:transform 0.18s ease, background 0.18s ease, box-shadow 0.18s ease; display:flex; align-items:center; justify-content:center; }
.mic-button i{ font-size:48px; }
.mic-button:hover{ transform:translateY(-1px); background:#1565C0; }
.mic-button:disabled{ background:#9E9E9E; cursor:not-allowed; box-shadow:none; transform:none; }
.mic-button.live{ background:#C62828; box-shadow:0 8px 18px rgba(198,40,40,0.28); }
.mic-button.live:hover{ background:#B71C1C; }
.status{ min-height:22px; color:#555; text-align:center; line-height:1.4; }
.status.error{ color:#C62828; }
.status.live{ color:#2E7D32; font-weight:500; }
.permission-panel{ max-width:760px; }
.permission-title{ margin:0 0 10px; font-size:1.15em; font-weight:500; color:#1976D2; }
.permission-message{ margin:0; color:#555; line-height:1.45; }
.permission-details{ color:#555; line-height:1.5; margin-top:14px; }
.permission-details p{ margin:0 0 12px; }
.origin-value{ font-family:monospace; font-weight:700; overflow-wrap:anywhere; }
.meter{ width:100%; height:8px; border-radius:999px; background:#ECEFF1; overflow:hidden; }
.meter span{ display:block; height:100%; width:0%; background:#2E7D32; transition:width 0.08s linear; }
.helper{ color:#666; font-size:0.92em; line-height:1.45; margin:8px 0 0; }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2,#mobile-header{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#content{ background-color:#121212; }
.card{ border-color:#333; background-color:#1E1E1E; }
.card h2{ color:#EDEDED; }
.info-row{ border-bottom-color:#333; }
.field label,.md-checkbox-container,.status,.helper{ color:#BBB; }
.permission-title{ color:#EDEDED; }
.permission-message,.permission-details{ color:#BBB; }
.tips-card p{ color:#BBB; }
.recipient-note{ color:#9E9E9E; }
.recipient-row.unavailable .md-checkmark{ border-color:#555; background:#2A2A2A; }
.field select{ background:#121212; color:#E0E0E0; border-color:#444; }
.md-checkmark{ border-color:#9AA0A6; background-color:#1E1E1E; }
.md-checkbox-container:hover input ~ .md-checkmark{ border-color:#E8EAED; }
.md-checkbox-container input:checked ~ .md-checkmark{ background-color:#8AB4F8; border-color:#8AB4F8; }
.md-checkmark:after{ border-color:#1E1E1E; }
.meter{ background:#333; }
}
"""

PAGING_SCRIPT = r"""
const senderName = __SENDER__;
const opsDemoMode = __OPS_DEMO_MODE__;
const micSelect = document.getElementById('microphoneSelect');
const micButton = document.getElementById('micButton');
const statusText = document.getElementById('statusText');
const meterBar = document.getElementById('meterBar');
const permissionPanel = document.getElementById('permissionPanel');
const permissionTitle = document.getElementById('permissionTitle');
const httpPermissionDetails = document.getElementById('httpPermissionDetails');
const serverOrigin = document.getElementById('serverOrigin');
const pagingLayout = document.getElementById('pagingLayout');
const pageTitle = document.getElementById('pageTitle');
const pageAll = document.getElementById('page_all');
const groupCheckboxes = Array.from(document.querySelectorAll('.group-checkbox'));
let audioContext = null;
let processor = null;
let source = null;
let stream = null;
let socket = null;
let processorSink = null;
let paging = false;
let sourceSampleRate = 48000;
let resampleCarry = [];
let frameCarry = new Uint8Array(0);

function setStatus(message, mode) {
  statusText.textContent = message;
  statusText.className = 'status' + (mode ? ' ' + mode : '');
}
function showPermissionMessage(title) {
  permissionTitle.textContent = title;
  httpPermissionDetails.style.display = 'none';
  permissionPanel.style.display = 'block';
  pagingLayout.classList.add('hidden');
  pageTitle.style.display = 'none';
}
function showHttpMicrophoneHelp() {
  permissionTitle.textContent = 'Your browser is likely blocking microphone access over HTTP';
  serverOrigin.textContent = window.location.origin;
  httpPermissionDetails.style.display = 'block';
  permissionPanel.style.display = 'block';
  pagingLayout.classList.add('hidden');
  pageTitle.style.display = 'none';
}
function showPagingControls() {
  permissionPanel.style.display = 'none';
  pagingLayout.classList.remove('hidden');
  pageTitle.style.display = 'block';
}
function selectedGroupId() {
  if (pageAll.checked) {
    const allGroups = groupCheckboxes.map(cb => cb.value).join('.');
    return allGroups || '0';
  }
  return groupCheckboxes.filter(cb => cb.checked).map(cb => cb.value).join('.');
}
function setControlsLocked(locked) {
  pageAll.disabled = locked;
  groupCheckboxes.forEach(cb => cb.disabled = locked || pageAll.checked || cb.dataset.unavailable === '1');
  micSelect.disabled = locked;
}
pageAll.addEventListener('change', () => {
  groupCheckboxes.forEach(cb => {
    cb.disabled = pageAll.checked || cb.dataset.unavailable === '1';
    if (pageAll.checked) cb.checked = false;
  });
});
async function loadMicrophones() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
    showPermissionMessage('Please allow microphone access in your browser settings to use this page');
    return;
  }
  const devices = await navigator.mediaDevices.enumerateDevices();
  const inputs = devices.filter(device => device.kind === 'audioinput');
  const currentValue = micSelect.value;
  micSelect.innerHTML = '<option value="">Default microphone</option>';
  inputs.forEach((device, index) => {
    const option = document.createElement('option');
    option.value = device.deviceId;
    option.textContent = device.label || `Microphone ${index + 1}`;
    micSelect.appendChild(option);
  });
  if (currentValue) micSelect.value = currentValue;
}
function ulawEncode(sample) {
  const BIAS = 0x84;
  const CLIP = 32635;
  let pcm = Math.max(-1, Math.min(1, sample));
  let value = Math.round(pcm * 32767);
  let sign = (value < 0) ? 0x80 : 0;
  if (value < 0) value = -value;
  if (value > CLIP) value = CLIP;
  value += BIAS;
  let exponent = 7;
  for (let mask = 0x4000; (value & mask) === 0 && exponent > 0; mask >>= 1) exponent--;
  const mantissa = (value >> (exponent + 3)) & 0x0F;
  return (~(sign | (exponent << 4) | mantissa)) & 0xFF;
}
function resampleToUlaw(input) {
  const combined = resampleCarry.length ? Float32Array.from([...resampleCarry, ...input]) : input;
  const ratio = sourceSampleRate / 8000;
  const frameCount = Math.floor(combined.length / ratio);
  const output = new Uint8Array(frameCount);
  let peak = 0;
  for (let i = 0; i < frameCount; i++) {
    const sample = combined[Math.floor(i * ratio)] || 0;
    peak = Math.max(peak, Math.abs(sample));
    output[i] = ulawEncode(sample);
  }
  const consumed = Math.floor(frameCount * ratio);
  resampleCarry = Array.from(combined.slice(consumed));
  meterBar.style.width = `${Math.min(100, Math.round(peak * 120))}%`;
  return output;
}
function websocketUrl(groupId) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const params = new URLSearchParams({ groups: groupId, sender: senderName });
  return `${protocol}//${window.location.host}/live?${params.toString()}`;
}
async function startPaging() {
  if (opsDemoMode) {
    if (window.openDemoModePopup) openDemoModePopup('paging');
    return;
  }
  const groupId = selectedGroupId();
  if (!groupId) {
    setStatus('Select at least one group before starting.', 'error');
    return;
  }
  try {
    micButton.disabled = true;
    setStatus('Requesting microphone permission...');
    const constraints = { audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } };
    if (micSelect.value) constraints.audio.deviceId = { exact: micSelect.value };
    stream = await navigator.mediaDevices.getUserMedia(constraints);
    await loadMicrophones();
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    if (audioContext.state === 'suspended') {
      await audioContext.resume();
    }
    sourceSampleRate = audioContext.sampleRate;
    source = audioContext.createMediaStreamSource(stream);
    processor = audioContext.createScriptProcessor(2048, 1, 1);
    processorSink = audioContext.createGain();
    processorSink.gain.value = 0;
    socket = new WebSocket(websocketUrl(groupId));
    socket.binaryType = 'arraybuffer';
    socket.onmessage = event => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'ready') {
          paging = true;
          micButton.classList.add('live');
          micButton.disabled = false;
          micButton.setAttribute('aria-label', 'End page');
          setControlsLocked(true);
          setStatus(
            message.pretone
              ? 'Playing pre-page tone... Paging is live. Press the microphone again to end.'
              : 'Paging is live. Press the microphone again to end.',
            'live'
          );
          source.connect(processor);
          processor.connect(processorSink);
          processorSink.connect(audioContext.destination);
        } else if (message.type === 'pretone_done') {
          setStatus('Paging is live. Press the microphone again to end.', 'live');
        } else if (message.type === 'error') {
          setStatus(message.message || 'Unable to start live page.', 'error');
          stopPaging();
        }
      } catch (error) {
        setStatus('Unexpected paging server response.', 'error');
        stopPaging();
      }
    };
    socket.onerror = () => {
      setStatus('Could not connect to the paging service.', 'error');
      stopPaging();
    };
    socket.onclose = () => {
      if (paging) setStatus('Paging ended.', '');
      stopPaging();
    };
    processor.onaudioprocess = event => {
      if (!paging || !socket || socket.readyState !== WebSocket.OPEN) return;
      const ulaw = resampleToUlaw(event.inputBuffer.getChannelData(0));
      const pending = new Uint8Array(frameCarry.length + ulaw.length);
      pending.set(frameCarry, 0);
      pending.set(ulaw, frameCarry.length);
      let offset = 0;
      for (; offset + 160 <= pending.length; offset += 160) {
        socket.send(pending.slice(offset, offset + 160));
      }
      frameCarry = pending.slice(offset);
    };
    setStatus('Connecting to paging service...');
  } catch (error) {
    setStatus(error && error.name === 'NotAllowedError' ? 'Microphone permission was denied.' : 'Unable to access the selected microphone.', 'error');
    stopPaging();
  }
}
async function requestInitialMicrophoneAccess() {
  showPermissionMessage('Requesting microphone access');
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (!window.isSecureContext) showHttpMicrophoneHelp();
    else showPermissionMessage('Please allow microphone access in your browser settings to use this page');
    return;
  }
  const startedAt = performance.now();
  try {
    const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    permissionStream.getTracks().forEach(track => track.stop());
    await loadMicrophones();
    showPagingControls();
    setStatus('Select recipients, then press the microphone.');
  } catch (error) {
    const elapsed = performance.now() - startedAt;
    if (!window.isSecureContext && elapsed < 700) showHttpMicrophoneHelp();
    else showPermissionMessage('Please allow microphone access in your browser settings to use this page');
  }
}
function stopPaging() {
  paging = false;
  micButton.classList.remove('live');
  micButton.disabled = false;
  micButton.setAttribute('aria-label', 'Start page');
  meterBar.style.width = '0%';
  setControlsLocked(false);
  if (processor) processor.disconnect();
  if (processorSink) processorSink.disconnect();
  if (source) source.disconnect();
  if (audioContext) audioContext.close();
  if (stream) stream.getTracks().forEach(track => track.stop());
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  processor = null;
  source = null;
  audioContext = null;
  stream = null;
  socket = null;
  processorSink = null;
  resampleCarry = [];
  frameCarry = new Uint8Array(0);
}
micButton.addEventListener('click', () => {
  if (paging) {
    setStatus('Ending page...');
    stopPaging();
    setStatus('Paging ended.');
  } else {
    startPaging();
  }
});
requestInitialMicrophoneAccess();
"""


def output_capable(endpoint):
    if endpoint.get("output_capable") is False:
        return False
    value = (str(endpoint.get("direction") or "") + " " + str(endpoint.get("input_type") or "")).lower()
    if "output" in value:
        return True
    capabilities = endpoint.get("capabilities") or []
    return isinstance(capabilities, list) and ("output" in capabilities or "bells" in capabilities)


def endpoint_can_receive_page(endpoint):
    if not output_capable(endpoint):
        return False
    if "available" in endpoint:
        return bool(endpoint.get("available"))
    return str(endpoint.get("status") or "").strip().lower() in {"online", "configured", "ready", "ok", "up"}


def group_member_tokens(members):
    return [token for token in re.split(r"[\s,]+", str(members or "")) if token]


def handle_request():
    user = require_non_receiver()
    if not isinstance(user, dict):
        return user
    ctx = legacy_user_context(user)
    data = ctx["settings"]
    username = user.get("username") or session.get("username") or "User"
    conn = db()
    try:
        with conn.cursor() as cur:
            groups = fetch_group_rows(cur)
    finally:
        conn.close()
    groups = filter_group_rows_for_user(user, groups)

    endpoint_data = endpoint_ipc("LIST_ENDPOINTS")
    endpoint_error = None if endpoint_data.get("ok", True) else endpoint_data.get("error") or "Endpoint manager returned an error."
    endpoint_availability = endpoint_availability_map(endpoint_data)
    all_unavailable = endpoint_error is None and not any_recipient_available(endpoint_availability)
    all_disabled = " disabled" if all_unavailable else ""
    all_unavailable_cls = " recipient-row unavailable" if all_unavailable else ""
    all_note = '<span class="recipient-note">No available recipients</span>' if all_unavailable else ""
    if groups:
        group_rows = []
        for group in groups:
            recipients = list(group_member_tokens(group.get("members")))
            if "paging" in set(group.get("monitor_categories") or []):
                for member in group_member_tokens(group.get("monitor_members")):
                    if member not in recipients:
                        recipients.append(member)
            online = sum(1 for member in recipients if group_member_available(member, endpoint_availability))
            has_online = endpoint_error is not None or online > 0
            row_cls = "" if has_online else " unavailable"
            disabled = "" if has_online else " disabled"
            unavailable_data = "0" if has_online else "1"
            note = "" if has_online else '<span class="recipient-note">No available recipients</span>'
            group_rows.append(
                f"""                <div class="info-row recipient-row{row_cls}">
                    <label class="md-checkbox-container">
                        <input type="checkbox" class="group-checkbox" value="{h(group.get("id"))}" data-unavailable="{unavailable_data}"{disabled}>
                        <span class="md-checkmark"></span>
                        <span class="text">{h(group.get("name"))}</span>
                        {note}
                    </label>
                </div>"""
            )
        group_html = "\n".join(group_rows)
    else:
        group_html = '<div class="info-row"><span class="helper">No groups are available.</span></div>'

    content = f"""    <h1 id="pageTitle" style="display:none;">Paging</h1>
    <div class="card permission-panel" id="permissionPanel">
        <p class="permission-title" id="permissionTitle">Requesting microphone access</p>
        <p class="permission-message">Microphone access is required to use this page</p>
        <div class="permission-details" id="httpPermissionDetails" style="display:none;">
            <p>The web server being used to serve the paging server's web interface does not support secure HTTPS. For security &amp; privacy purposes, most modern browsers block sensitive permissions over insecure HTTP including microphone. There is probably nothing you can do about this. Contact your system administrator to have them enable HTTPS support. You may be able to tell your browser to add this server as an exception, however, this is highly discouraged by most browser vendors and usually requires disabling built-in security or using developer options and triggers security warnings.</p>
            <p>Server Origin: <span class="origin-value" id="serverOrigin"></span></p>
        </div>
    </div>
    <div class="layout hidden" id="pagingLayout">
        <div class="card">
            <h2>Recipients</h2>
            <div class="info-row{all_unavailable_cls}">
                <label class="md-checkbox-container">
                    <input type="checkbox" id="page_all" value="1"{all_disabled}>
                    <span class="md-checkmark"></span>
                    <span class="text" style="font-weight:bold;color:#1976D2;">All Recipients</span>
                    {all_note}
                </label>
            </div>
{group_html}
        </div>
        <div>
            <div class="card">
                <h2>Microphone</h2>
                <div class="field">
                    <label for="microphoneSelect">Input device</label>
                    <select id="microphoneSelect">
                        <option value="">Default microphone</option>
                    </select>
                </div>
                <div class="page-control">
                    <button type="button" class="mic-button" id="micButton" aria-label="Start page">
                        <i class="fa-solid fa-microphone"></i>
                    </button>
                    <div class="meter"><span id="meterBar"></span></div>
                    <div class="status" id="statusText">Select recipients, then press the microphone.</div>
                </div>
            </div>
            <div class="card tips-card">
                <h2>General tips for paging</h2>
                <p>Avoid using low quailty microphones if possible such as ones from lower-end laptops and webcams. These can cause interference, feedback, or loud background noise.</p>
                <p>If you are using a senstive microphone and/or are located near a device playing the page, you will likely have bad echo. If possible keep the microphone away from any phones or speakers. Feedback is audible on every speakers receiving the page, not just the one by the microphone.</p>
                <p>Speak directly into your device or microphone but avoid being right next to the microphone. The distance needed varries per setup.</p>
                <p>Stop any media playback on your device before making the page. Avoid using the keyboard during the page if possible for microphones on laptops or on microphones near keyboards.</p>
            </div>
        </div>
    </div>"""
    script = PAGING_SCRIPT.replace("__SENDER__", json.dumps(username)).replace("__OPS_DEMO_MODE__", "true" if demo_mode_enabled() else "false")
    return legacy_page("Paging", ctx, "paging", PAGING_STYLE, content, script)
