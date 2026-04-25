<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);

session_start();
require_once '/var/www/html/config.php';

$is_insecure = (!isset($_SERVER['HTTPS']) || $_SERVER['HTTPS'] !== 'on');
if (isset($_SERVER['HTTP_X_FORWARDED_PROTO']) && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https') {
    $is_insecure = false;
}

if (!isset($_SESSION['user_id'])) {
    header("Location: /");
    exit;
}

$stmt = $pdo->prepare("SELECT role FROM users WHERE id = :id LIMIT 1");
$stmt->execute(['id' => $_SESSION['user_id']]);
$userRole = $stmt->fetchColumn();
$isAdmin = ($userRole === 'admin' || $userRole === 'tempadmin');
if (!$isAdmin) {
    http_response_code(403);
    header('Content-Type: text/html; charset=UTF-8');
    readfile('/var/www/html/.errors/403.html');
    exit;
}
$username = $_SESSION['username'] ?? 'User';

$stmt = $pdo->query("SELECT path, webpath, webroles, webinterface, webname, webicon FROM enabledmodules WHERE status = 1 ORDER BY path ASC");
$modules = $stmt->fetchAll(PDO::FETCH_ASSOC);

$stmt = $pdo->query("SELECT parameter, value FROM systemsettings");
$settings = [];
foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
    $settings[$row['parameter']] = $row['value'];
}

$product_name = $settings['product_name'] ?? 'Open Paging Server';
$favicon = $settings['favicon'] ?? '';
$show_online_docs = $settings['show_online_docs'] ?? '1';

function is_port_in_use($port) {
    $connection = @fsockopen('127.0.0.1', $port, $errno, $errstr, 0.1);
    if (is_resource($connection)) {
        fclose($connection);
        return true;
    }
    return false;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['save_sip_settings'])) {
    $sip_enabled = isset($_POST['sip']) ? '1' : '0';
    $udp_tcp_enabled = isset($_POST['enable_insecure_sip']) ? '1' : '0';
    $udp_tcp_port = $_POST['insecure_sip_port'] ?? '5060';
    $tls_enabled = isset($_POST['enable_secure_sip']) ? '1' : '0';
    $tls_port = $_POST['secure_sip_port'] ?? '5061';

    $old_udp_port = $settings['insecure_sip_port'] ?? '5060';
    $old_tls_port = $settings['secure_sip_port'] ?? '5061';

    $errors = [];
    if ($udp_tcp_enabled && $udp_tcp_port != $old_udp_port) {
        if (is_port_in_use($udp_tcp_port)) {
            $errors[] = "Port $udp_tcp_port is already in use.";
        }
    }
    if ($tls_enabled && $tls_port != $old_tls_port) {
        if (is_port_in_use($tls_port)) {
            $errors[] = "Port $tls_port is already in use.";
        }
    }

    if (!empty($errors)) {
        if (isset($_SERVER['HTTP_X_REQUESTED_WITH']) && $_SERVER['HTTP_X_REQUESTED_WITH'] === 'XMLHttpRequest') {
            echo json_encode(['status' => 'error', 'message' => implode(' ', $errors)]);
            exit;
        }
    } elseif (($udp_tcp_enabled && ($udp_tcp_port < 1 || $udp_tcp_port > 65535)) || 
        ($tls_enabled && ($tls_port < 1 || $tls_port > 65535))) {
        if (isset($_SERVER['HTTP_X_REQUESTED_WITH']) && $_SERVER['HTTP_X_REQUESTED_WITH'] === 'XMLHttpRequest') {
            echo json_encode(['status' => 'error', 'message' => 'Invalid port range.']);
            exit;
        }
    } else {
        $stmt = $pdo->prepare("UPDATE systemsettings SET value = :value WHERE parameter = :parameter");
        $stmt->execute(['value' => $sip_enabled, 'parameter' => 'sip']);
        $stmt->execute(['value' => $udp_tcp_enabled, 'parameter' => 'enable_insecure_sip']);
        $stmt->execute(['value' => $udp_tcp_port, 'parameter' => 'insecure_sip_port']);
        $stmt->execute(['value' => $tls_enabled, 'parameter' => 'enable_secure_sip']);
        $stmt->execute(['value' => $tls_port, 'parameter' => 'secure_sip_port']);

        if (isset($_SERVER['HTTP_X_REQUESTED_WITH']) && $_SERVER['HTTP_X_REQUESTED_WITH'] === 'XMLHttpRequest') {
            echo json_encode(['status' => 'success']);
            exit;
        }
    }
}

$udpTcpEnabled = ($settings['enable_insecure_sip'] ?? '0') === '1';
$udpTcpPort = $settings['insecure_sip_port'] ?? '5060';
$tlsEnabled = ($settings['enable_secure_sip'] ?? '0') === '1';
$tlsPort = $settings['secure_sip_port'] ?? '5061';
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>SIP Settings - <?= htmlspecialchars($product_name) ?></title>
<?php if (!empty($favicon)): ?>
<link rel="icon" href="<?= htmlspecialchars($favicon) ?>" type="image/x-icon">
<?php endif; ?>
<link href="https://fonts.googleapis.com/css?family=Roboto:300,400,500,700&display=swap" rel="stylesheet" />
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet" />
<style>
body, html { margin:0; padding:0; font-family:"Tahoma",sans-serif; font-weight:300; background-color:#FFF; height:100%; }
strong { font-weight:700; }
#sidebar { width:220px; background-color:#1976D2; color:#FFF; height:100vh; position:fixed; top:0; left:0; display:flex; flex-direction:column; box-shadow:2px 0 8px rgba(0,0,0,0.2); transition:transform 0.3s ease; z-index:1200; }
@media (max-width:767px){ #sidebar{ transform:translateX(-100%); } #sidebar.open{ transform:translateX(0); } }
#sidebar h2 { text-align:center; padding:20px 0; margin:0; font-weight:500; background-color:#1565C0; font-size:1.2em; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#FFF; padding:12px 20px; display:block; border-bottom:1px solid rgba(255,255,255,0.1); text-decoration:none; transition:background 0.3s; font-size:0.9em; text-align:left; box-sizing:border-box; }
#sidebar a i,.logout-btn i,.logout-btn-mobile i,.admin-only i { margin-right:8px; width:20px; }
#sidebar a:hover,#sidebar a.active{ background-color:#1565C0; }
.logout-btn{ background-color:#C62828; border:none; cursor:pointer; margin-top:auto; transition:background-color 0.3s; }
.logout-btn:hover{ background-color:#B71C1C; }
.logout-btn:active{ background-color:#A51B1B; }
.logout-btn-mobile{ background-color:#C62828; border:none; cursor:pointer; transition:background-color 0.3s; display:none; }
.logout-btn-mobile:hover{ background-color:#B71C1C; }
@media(max-width:767px){ .logout-btn{ display:none; } .logout-btn-mobile{ display:block; } }
#mobile-header{ display:flex; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h2{ color:#1976D2; margin-bottom:16px; font-weight:400; }
#content h1{ font-weight:400; }
.info-card{ background:#FFF; padding:16px; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; }
.info-row { display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid #f0f0f0; align-items: center; }
.info-row:last-child { border-bottom:none; }
.info-label { font-weight:500; color:#555; }

.tabs-container { margin-bottom: 20px; border-bottom: 1px solid #DDD; }
.tabs-desktop { display: flex; gap: 10px; }
.tab-link { padding: 10px 20px; cursor: pointer; border: 1px solid transparent; border-bottom: none; border-radius: 5px 5px 0 0; background: #f5f5f5; color: #555; transition: 0.3s; text-decoration: none; }
.tab-link.active { background: #1976D2; color: #FFF; border-color: #1976D2; }
.tabs-mobile { display: none; width: 100%; padding: 10px; border-radius: 5px; border: 1px solid #CCC; margin-bottom: 15px; font-size: 16px; }
.tab-content { display: none; }
.tab-content.active { display: block; }

.switch { position: relative; display: inline-block; width: 36px; height: 14px; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .4s; border-radius: 14px; }
.slider:before { position: absolute; content: ""; height: 20px; width: 20px; left: -2px; bottom: -3px; background-color: white; transition: .4s; border-radius: 50%; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
input:checked + .slider { background-color: #90caf9; }
input:checked + .slider:before { transform: translateX(20px); background-color: #1976D2; }

.port-error-text { color: #F44336; font-size: 0.8em; margin-top: 4px; display: none; }
.invalid-port { border-color: #F44336 !important; background-color: rgba(244, 67, 54, 0.05) !important; }

@media(max-width:767px){
    .tabs-desktop { display: none; }
    .tabs-mobile { display: block; }
}

@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#mobile-header h2{ color:#FFF; }
#content{ background-color:#121212; }
.info-card{ border:1px solid #333; background-color:#1E1E1E; }
h2,h3{ color:#BB86FC; }
.info-label { color:#BBB; }
.info-row { border-bottom:1px solid #333; }
.tabs-container { border-bottom-color: #333; }
.tab-link { background: #333; color: #BBB; }
.tab-link.active { background: #BB86FC; color: #000; }
.tabs-mobile { background: #1E1E1E; color: #E0E0E0; border-color: #444; }
input:checked + .slider { background-color: #3d2b52; }
input:checked + .slider:before { background-color: #BB86FC; }
.port-error-text { color: #ff5252; }
.invalid-port { border-color: #ff5252 !important; }
}

.login-settings input[type="text"],
.login-settings input[type="number"],
.login-settings select,
.login-settings textarea {
    width:100%;
    padding:10px;
    border-radius:6px;
    border:1px solid #CCC;
    font-family:inherit;
    font-size:14px;
    box-sizing:border-box;
}

.login-settings button {
    background:#1976D2;
    color:#FFF;
    border:none;
    padding:10px 16px;
    border-radius:6px;
    font-size:14px;
    cursor:pointer;
}

.login-settings button:hover {
    background:#1565C0;
}

@media(prefers-color-scheme:dark){
    .login-settings input[type="text"],
    .login-settings input[type="number"],
    .login-settings select,
    .login-settings textarea {
        background:#1E1E1E;
        border:1px solid #444;
        color:#E0E0E0;
    }

    .login-settings input:disabled,
    .login-settings select:disabled,
    .login-settings textarea:disabled {
        background:#2A2A2A;
        color:#777;
    }

    .login-settings button {
        background:#BB86FC;
        color:#000;
    }

    .login-settings button:hover {
        background:#A370F7;
    }
}

#sip-save-status { margin-left: 10px; font-size: 0.85em; transition: opacity 0.5s; }
</style>
</head>
<body>
<div id="mobile-header">
    <span class="hamburger" onclick="toggleSidebar()"><i class="fa-solid fa-bars"></i></span>
    <h2><?= htmlspecialchars($product_name) ?></h2>
</div>
<div id="overlay" onclick="closeSidebar()"></div>
<div id="sidebar">
    <h2><?= htmlspecialchars($product_name) ?></h2>
    <a href="/dashboard.php"><i class="fa-solid fa-house"></i> Dashboard</a>
    <a href="/paging.php"><i class="fa-solid fa-bullhorn"></i> Paging</a>
    <a href="/messages"><i class="fa-solid fa-message"></i> Messages</a>
    <a href="/history.php"><i class="fa-solid fa-clock-rotate-left"></i> History</a>

    <?php foreach ($modules as $mod):
        if ($mod['webinterface'] != 1) continue;
        $allowedRoles = array_map('trim', explode(',', $mod['webroles']));
        if (!in_array($userRole, $allowedRoles)) continue;
        $link = htmlspecialchars($mod['webpath']);
        $name = htmlspecialchars($mod['webname']);
        $icon = htmlspecialchars($mod['webicon']) ?: 'fa-circle';
    ?>
        <a href="<?php echo $link; ?>">
            <i class="fa-solid <?php echo $icon; ?>"></i> <?php echo $name; ?>
        </a>
    <?php endforeach; ?>

    <?php if ($isAdmin): ?>
      <a href="/admin/manage-users.php" class="admin-only"><i class="fa-solid fa-users-cog"></i> Manage Users</a>
      <a href="/admin/manage-endpoints.php" class="admin-only"><i class="fa-solid fa-shapes"></i> Manage Endpoints</a>
      <a href="/admim/manage-groups.php"><i class="fa-solid fa-user-group"></i> Manage Groups</a>
      <a href="/admin/settings/general.php" class="active admin-only"><i class="fa-solid fa-cogs"></i> Server Settings</a>
    <?php endif; ?>
    
    <?php if ($show_online_docs == '1'): ?>
    <a href="https://docs.openpagingserver.org"><i class="fa-solid fa-book"></i> Online Documentation</a>
    <?php endif; ?>

    <button class="logout-btn-mobile" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
    <button class="logout-btn" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
</div>

<div id="content" onclick="closeSidebarOnContentClick()">
    <h1>Settings</h1>

    <div class="tabs-container">
        <div class="tabs-desktop">
            <a href="general.php" class="tab-link">General</a>
            <a href="login.php" class="tab-link">Login</a>
            <a href="sip.php" class="tab-link active">SIP</a>
            <a href="branding.php" class="tab-link">Branding</a>
            <a href="about.php" class="tab-link">About</a>
        </div>
        <select class="tabs-mobile" onchange="window.location.href=this.value">
            <option value="general.php">General</option>
            <option value="login.php">Login</option>
            <option value="sip.php">SIP</option>
            <option value="branding.php" selected>Branding</option>
            <option value="about.php">About</option>
        </select>
    </div>

    <div id="sip" class="tab-content active">
        <div class="info-card login-settings">
            <p><?= htmlspecialchars($product_name) ?> uses Session Initiation Protocol (SIP) to integrate with PBXes and phone systems, connect to consoles, and to ATAs.</p>
            
            <form id="sipSettingsForm">

                <div class="info-row" style="border-bottom:none; padding-bottom:0;">
                    <span class="info-label">Enable SIP over UDP/TCP</span>
                    <span>
                        <label class="switch">
                            <input type="checkbox" name="enable_insecure_sip" id="udpToggle" <?= $udpTcpEnabled ? 'checked' : '' ?>>
                            <span class="slider"></span>
                        </label>
                    </span>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; margin-bottom:16px;">
                    <span class="info-label">Port</span>
                    <input type="number" name="insecure_sip_port" id="udpPort" min="1" max="65535" value="<?= htmlspecialchars($udpTcpPort) ?>" <?= !$udpTcpEnabled ? 'disabled' : '' ?>>
                    <span id="udpPortError" class="port-error-text">Please enter a valid port (1-65535).</span>
                </div>

                <div class="info-row" style="border-bottom:none; padding-bottom:0;">
                    <span class="info-label">Enable SIP over TLS</span>
                    <span>
                        <label class="switch">
                            <input type="checkbox" name="enable_secure_sip" id="tlsToggle" <?= $tlsEnabled ? 'checked' : '' ?>>
                            <span class="slider"></span>
                        </label>
                    </span>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                    <span class="info-label">Port</span>
                    <input type="number" name="secure_sip_port" id="tlsPort" min="1" max="65535" value="<?= htmlspecialchars($tlsPort) ?>" <?= !$tlsEnabled ? 'disabled' : '' ?>>
                    <span id="tlsPortError" class="port-error-text">Please enter a valid port (1-65535).</span>
                </div>

                <input type="hidden" name="save_sip_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveSipBtn">Save Settings</button>
                    <span id="sip-save-status"></span>
                </div>
            </form>
        </div>
    </div>
</div>

<script>
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

document.addEventListener('DOMContentLoaded', function() {
    const udpToggle = document.getElementById('udpToggle');
    const udpPortInput = document.getElementById('udpPort');
    const udpPortError = document.getElementById('udpPortError');
    const tlsToggle = document.getElementById('tlsToggle');
    const tlsPortInput = document.getElementById('tlsPort');
    const tlsPortError = document.getElementById('tlsPortError');
    const saveSipBtn = document.getElementById('saveSipBtn');
    const sipStatusText = document.getElementById('sip-save-status');

    function validatePortInput(input, errorElement) {
        let val = parseInt(input.value);
        if (input.value === "" || isNaN(val) || val < 1 || val > 65535) {
            input.classList.add('invalid-port');
            errorElement.style.display = 'block';
            return false;
        } else {
            input.classList.remove('invalid-port');
            errorElement.style.display = 'none';
            return true;
        }
    }

    [udpPortInput, tlsPortInput].forEach(input => {
        input.addEventListener('input', function() {
            if (this.value > 65535) this.value = 65535;
            if (this.value.length > 5) this.value = this.value.slice(0, 5);
            const err = this.id === 'udpPort' ? udpPortError : tlsPortError;
            validatePortInput(this, err);
        });
    });

    if(udpToggle){
        udpToggle.addEventListener('change', function() {
            udpPortInput.disabled = !this.checked;
            if(!this.checked) {
                udpPortInput.classList.remove('invalid-port');
                udpPortError.style.display = 'none';
            } else {
                validatePortInput(udpPortInput, udpPortError);
            }
        });
    }

    if(tlsToggle){
        tlsToggle.addEventListener('change', function() {
            tlsPortInput.disabled = !this.checked;
            if(!this.checked) {
                tlsPortInput.classList.remove('invalid-port');
                tlsPortError.style.display = 'none';
            } else {
                validatePortInput(tlsPortInput, tlsPortError);
            }
        });
    }

    if(saveSipBtn){
        saveSipBtn.addEventListener('click', function() {
            let isValid = true;
            if (udpToggle.checked && !validatePortInput(udpPortInput, udpPortError)) isValid = false;
            if (tlsToggle.checked && !validatePortInput(tlsPortInput, tlsPortError)) isValid = false;

            if (!isValid) {
                sipStatusText.innerText = "Please fix port errors.";
                sipStatusText.style.color = "#F44336";
                setTimeout(() => { sipStatusText.innerText = ""; }, 3000);
                return;
            }

            const formData = new FormData(document.getElementById('sipSettingsForm'));
            saveSipBtn.disabled = true;
            sipStatusText.innerText = "Saving...";
            sipStatusText.style.color = "inherit";

            fetch(window.location.href, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(response => response.json())
            .then(data => {
                if(data.status === 'success') {
                    sipStatusText.innerText = "SIP Settings saved.";
                    sipStatusText.style.color = "#4CAF50";
                } else {
                    sipStatusText.innerText = data.message || "Error saving.";
                    sipStatusText.style.color = "#F44336";
                }
            })
            .catch(error => {
                sipStatusText.innerText = "Connection error.";
                sipStatusText.style.color = "#F44336";
            })
            .finally(() => {
                saveSipBtn.disabled = false;
                setTimeout(() => { sipStatusText.innerText = ""; }, 3000);
            });
        });
    }
    
    if(udpToggle && udpToggle.checked) validatePortInput(udpPortInput, udpPortError);
    if(tlsToggle && tlsToggle.checked) validatePortInput(tlsPortInput, tlsPortError);
});
</script>
</body>
</html>
