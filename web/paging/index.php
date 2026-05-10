<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);

session_start();
require_once __DIR__ . '/../config.php';
require_once __DIR__ . '/../includes/sidebar-brand.php';

if (!isset($_SESSION['user_id'])) {
    header("Location: /");
    exit;
}

$stmt = $pdo->prepare("SELECT role, username FROM users WHERE id = :id LIMIT 1");
$stmt->execute(['id' => $_SESSION['user_id']]);
$user = $stmt->fetch(PDO::FETCH_ASSOC);
$userRole = $user['role'] ?? '';
$isReceiver = ($userRole === 'receiver' || $userRole === 'tempreceiver');
if ($isReceiver) {
    header("Location: /dashboard.php");
    exit;
}
$username = $user['username'] ?? ($_SESSION['username'] ?? 'User');
$isAdmin = ($userRole === 'admin' || $userRole === 'tempadmin');

$stmt = $pdo->query("SELECT parameter, value FROM systemsettings");
$settings = [];
foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
    $settings[$row['parameter']] = $row['value'];
}
$product_name = $settings['product_name'] ?? 'Open Paging Server';
$favicon = $settings['favicon'] ?? '';
$show_online_docs = $settings['show_online_docs'] ?? '1';
$livepage_ws_port = $settings['livepage_ws_port'] ?? '50010';

$stmt = $pdo->query("SELECT id, name, members FROM groups ORDER BY name ASC");
$groups = $stmt->fetchAll(PDO::FETCH_ASSOC);


function endpoint_manager_request($command) {
    $socket = @fsockopen('127.0.0.1', 50000, $errno, $errstr, 2);
    if (!$socket) {
        return [null, "Endpoint manager is not reachable: $errstr"];
    }
    stream_set_timeout($socket, 5);
    fwrite($socket, $command . "\n");
    $response = '';
    while (!feof($socket)) {
        $response .= fgets($socket, 65536);
        if (substr($response, -1) === "\n") {
            break;
        }
    }
    fclose($socket);
    $decoded = json_decode($response, true);
    if (!is_array($decoded)) {
        return [null, "Endpoint manager returned an invalid response."];
    }
    return [$decoded, null];
}

function output_capable($endpoint) {
    if (array_key_exists('output_capable', $endpoint) && !$endpoint['output_capable']) {
        return false;
    }
    $value = strtolower(trim(($endpoint['direction'] ?? '') . ' ' . ($endpoint['input_type'] ?? '')));
    if (strpos($value, 'output') !== false) {
        return true;
    }
    $capabilities = $endpoint['capabilities'] ?? [];
    return is_array($capabilities) && (in_array('output', $capabilities, true) || in_array('bells', $capabilities, true));
}

function endpoint_can_receive_page($endpoint) {
    if (!output_capable($endpoint)) {
        return false;
    }
    $status = strtolower(trim($endpoint['status'] ?? ''));
    return in_array($status, ['online', 'configured', 'ready', 'ok', 'up'], true);
}

function group_member_tokens($members) {
    return preg_split('/[\s,]+/', $members ?? '', -1, PREG_SPLIT_NO_EMPTY);
}

[$endpointData, $endpointError] = endpoint_manager_request('LIST_ENDPOINTS');
$endpointAvailability = [];
if (is_array($endpointData)) {
    foreach (($endpointData['modules'] ?? []) as $moduleInfo) {
        $moduleName = trim($moduleInfo['module'] ?? '');
        if ($moduleName === '') {
            continue;
        }
        foreach (($moduleInfo['endpoints'] ?? []) as $endpoint) {
            $endpointId = trim($endpoint['id'] ?? '');
            if ($endpointId === '') {
                continue;
            }
            $endpointAvailability[$moduleName . '/' . $endpointId] = endpoint_can_receive_page($endpoint);
        }
    }
}

$totalOnlineRecipients = 0;
foreach ($endpointAvailability as $canReceivePage) {
    if ($canReceivePage) {
        $totalOnlineRecipients++;
    }
}

foreach ($groups as &$group) {
    $onlineRecipients = 0;
    foreach (group_member_tokens($group['members'] ?? '') as $member) {
        if (!empty($endpointAvailability[$member])) {
            $onlineRecipients++;
        }
    }
    $group['online_recipients'] = $onlineRecipients;
    $group['has_online_recipients'] = $endpointError !== null || $onlineRecipients > 0;
}
unset($group);
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Paging - <?= htmlspecialchars($product_name) ?></title>
<?php if (!empty($favicon)): ?>
<link rel="icon" href="<?= htmlspecialchars($favicon) ?>" type="image/x-icon">
<?php endif; ?>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet" />
<link href="/assets/sidebar-brand.css" rel="stylesheet" />
<style>
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
.md-checkbox-container{ display:flex; align-items:center; position:relative; cursor:pointer; font-size:14px; font-weight:500; color:#555; user-select:none; width:100%; padding:5px 0; }
.md-checkbox-container input{ position:absolute; opacity:0; cursor:pointer; height:0; width:0; }
.md-checkmark{ position:relative; display:inline-block; flex:0 0 auto; height:20px; width:20px; background-color:#fff; border:2px solid #5f6368; border-radius:2px; margin-right:12px; transition:all 0.2s; }
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
.permission-details ol{ margin:8px 0 0 22px; padding:0; }
.permission-details li{ margin-bottom:8px; }
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
.card h2{ color:#BB86FC; }
.info-row{ border-bottom-color:#333; }
.field label,.md-checkbox-container,.status,.helper{ color:#BBB; }
.permission-title{ color:#BB86FC; }
.permission-message,.permission-details{ color:#BBB; }
.tips-card p{ color:#BBB; }
.recipient-note{ color:#9E9E9E; }
.recipient-row.unavailable .md-checkmark{ border-color:#555; background:#2A2A2A; }
.field select{ background:#121212; color:#E0E0E0; border-color:#444; }
.md-checkmark{ border-color:#9AA0A6; background-color:#1E1E1E; }
.md-checkbox-container input:checked ~ .md-checkmark{ background-color:#8AB4F8; border-color:#8AB4F8; }
.md-checkmark:after{ border-color:#1E1E1E; }
.meter{ background:#333; }
}
</style>
</head>
<body>
<div id="mobile-header">
    <span class="hamburger" onclick="toggleSidebar()"><i class="fa-solid fa-bars"></i></span>
    <?= ops_sidebar_brand_html($settings, $product_name) ?>
</div>
<div id="overlay" onclick="closeSidebar()"></div>
<div id="sidebar">
    <?= ops_sidebar_brand_html($settings, $product_name) ?>
    <a href="/dashboard.php"><i class="fa-solid fa-house"></i> Dashboard</a>
    <a href="/paging" class="active"><i class="fa-solid fa-bullhorn"></i> Paging</a>
    <a href="/messages"><i class="fa-solid fa-message"></i> Messages</a>
    <a href="/history"><i class="fa-solid fa-clock-rotate-left"></i> History</a>
    <a href="/bells"><i class="fa-solid fa-bell"></i> Bells</a>
    <a href="/assets/"><i class="fa-solid fa-folder-open"></i> Assets</a>
    <?php if ($isAdmin): ?>
      <a href="/admin/manage-users.php" class="admin-only"><i class="fa-solid fa-users-cog"></i> Manage Users</a>
      <a href="/admin/manage-endpoints.php" class="admin-only"><i class="fa-solid fa-shapes"></i> Manage Endpoints</a>
      <a href="/admin/manage-groups.php" class="admin-only"><i class="fa-solid fa-user-group"></i> Manage Groups</a>
      <a href="/admin/settings/general.php" class="admin-only"><i class="fa-solid fa-cogs"></i> Server Settings</a>
    <?php endif; ?>
    <?php if ($show_online_docs == '1'): ?>
    <a href="https://docs.openpagingserver.org"><i class="fa-solid fa-book"></i> Online Documentation</a>
    <?php endif; ?>
    <button class="logout-btn-mobile" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
    <button class="logout-btn" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
</div>
<div id="content" onclick="closeSidebarOnContentClick()">
    <h1 id="pageTitle" style="display:none;">Paging</h1>
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
            <div class="info-row <?= ($endpointError === null && $totalOnlineRecipients === 0) ? 'recipient-row unavailable' : '' ?>">
                <label class="md-checkbox-container">
                    <input type="checkbox" id="page_all" value="1" <?= ($endpointError === null && $totalOnlineRecipients === 0) ? 'disabled' : '' ?>>
                    <span class="md-checkmark"></span>
                    <span class="text" style="font-weight:bold;color:#1976D2;">All Recipients</span>
                    <?php if ($endpointError === null && $totalOnlineRecipients === 0): ?>
                        <span class="recipient-note">No online recipients</span>
                    <?php endif; ?>
                </label>
            </div>
            <?php if (empty($groups)): ?>
                <div class="info-row"><span class="helper">No groups are available.</span></div>
            <?php else: ?>
                <?php foreach ($groups as $group): ?>
                    <div class="info-row recipient-row <?= $group['has_online_recipients'] ? '' : 'unavailable' ?>">
                        <label class="md-checkbox-container">
                            <input type="checkbox" class="group-checkbox" value="<?= htmlspecialchars($group['id']) ?>" <?= $group['has_online_recipients'] ? '' : 'disabled' ?>>
                            <span class="md-checkmark"></span>
                            <span class="text"><?= htmlspecialchars($group['name']) ?></span>
                            <?php if (!$group['has_online_recipients']): ?>
                                <span class="recipient-note">No online recipients</span>
                            <?php endif; ?>
                        </label>
                    </div>
                <?php endforeach; ?>
            <?php endif; ?>
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
    </div>
</div>
<script>
const wsPort = <?= json_encode((int)$livepage_ws_port) ?>;
const senderName = <?= json_encode($username) ?>;
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
let paging = false;
let sourceSampleRate = 48000;
let resampleCarry = [];
let frameCarry = new Uint8Array(0);

function toggleSidebar() {
  const sidebar = document.getElementById("sidebar");
  sidebar.classList.toggle("open");
  document.getElementById("overlay").classList.toggle("active", sidebar.classList.contains("open"));
}
function closeSidebar() {
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("overlay").classList.remove("active");
}
function closeSidebarOnContentClick() {
  if (document.getElementById("sidebar").classList.contains("open")) closeSidebar();
}
function logout() {
  window.location.href = "/logout.php";
}
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
  if (pageAll.checked) return '0';
  return groupCheckboxes.filter(cb => cb.checked).map(cb => cb.value).join('.');
}
function setControlsLocked(locked) {
  pageAll.disabled = locked;
  groupCheckboxes.forEach(cb => cb.disabled = locked || pageAll.checked);
  micSelect.disabled = locked;
}
pageAll.addEventListener('change', () => {
  groupCheckboxes.forEach(cb => {
    cb.disabled = pageAll.checked;
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
  return `${protocol}//${window.location.hostname}:${wsPort}/live?${params.toString()}`;
}
async function startPaging() {
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
    sourceSampleRate = audioContext.sampleRate;
    source = audioContext.createMediaStreamSource(stream);
    processor = audioContext.createScriptProcessor(2048, 1, 1);
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
          setStatus('Paging is live. Press the microphone again to end.', 'live');
          source.connect(processor);
          processor.connect(audioContext.destination);
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
    if (!window.isSecureContext) {
      showHttpMicrophoneHelp();
    } else {
      showPermissionMessage('Please allow microphone access in your browser settings to use this page');
    }
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
    if (!window.isSecureContext && elapsed < 700) {
      showHttpMicrophoneHelp();
    } else {
      showPermissionMessage('Please allow microphone access in your browser settings to use this page');
    }
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
  if (source) source.disconnect();
  if (audioContext) audioContext.close();
  if (stream) stream.getTracks().forEach(track => track.stop());
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  processor = null;
  source = null;
  audioContext = null;
  stream = null;
  socket = null;
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
</script>
</body>
</html>
