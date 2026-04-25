<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);

session_start();
require_once '/var/www/html/config.php';

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

$stmt = $pdo->query("SELECT parameter, value FROM systemsettings");
$settings = [];
foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
    $settings[$row['parameter']] = $row['value'];
}

$product_name = $settings['product_name'] ?? 'Open Paging Server';
$favicon = $settings['favicon'] ?? '';
$show_online_docs = $settings['show_online_docs'] ?? '1';

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['save_general_settings'])) {
    $docs_enabled = isset($_POST['show_online_docs']) ? '1' : '0';
    $stmt = $pdo->prepare("UPDATE systemsettings SET value = :value WHERE parameter = 'show_online_docs'");
    $stmt->execute(['value' => $docs_enabled]);
    
    if (isset($_SERVER['HTTP_X_REQUESTED_WITH']) && $_SERVER['HTTP_X_REQUESTED_WITH'] === 'XMLHttpRequest') {
        header('Content-Type: application/json');
        ob_clean();
        echo json_encode(['status' => 'success']);
        exit;
    }
}

$stmt = $pdo->query("SELECT path, webpath, webroles, webinterface, webname, webicon FROM enabledmodules WHERE status = 1 ORDER BY path ASC");
$modules = $stmt->fetchAll(PDO::FETCH_ASSOC);
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>General Settings - <?= htmlspecialchars($product_name) ?></title>
<?php if (!empty($favicon)): ?>
<link rel="icon" href="<?= htmlspecialchars($favicon) ?>" type="image/x-icon">
<?php endif; ?>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet" />
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
#mobile-header{ display:flex; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
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
.switch { position: relative; display: inline-block; width: 36px; height: 14px; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .4s; border-radius: 14px; }
.slider:before { position: absolute; content: ""; height: 20px; width: 20px; left: -2px; bottom: -3px; background-color: white; transition: .4s; border-radius: 50%; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
input:checked + .slider { background-color: #90caf9; }
input:checked + .slider:before { transform: translateX(20px); background-color: #1976D2; }
@media(max-width:767px){ .tabs-desktop { display: none; } .tabs-mobile { display: block; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.info-card{ border:1px solid #333; background-color:#1E1E1E; }
.info-label { color:#BBB; }
.info-row { border-bottom:1px solid #333; }
.tabs-container { border-bottom-color: #333; }
.tab-link { background: #333; color: #BBB; }
.tab-link.active { background: #BB86FC; color: #000; }
.tabs-mobile { background: #1E1E1E; color: #E0E0E0; border-color: #444; }
input:checked + .slider { background-color: #3d2b52; }
input:checked + .slider:before { background-color: #BB86FC; }
}
.login-settings button { background:#1976D2; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; }
.login-settings button:hover { background:#1565C0; }
@media(prefers-color-scheme:dark){ .login-settings button { background:#BB86FC; color:#000; } .login-settings button:hover { background:#A370F7; } }
#general-save-status { margin-left: 10px; font-size: 0.85em; transition: opacity 0.5s; }
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
    <a href="/paging"><i class="fa-solid fa-bullhorn"></i> Paging</a>
    <a href="/messages"><i class="fa-solid fa-message"></i> Messages</a>
    <a href="/history"><i class="fa-solid fa-clock-rotate-left"></i> History</a>
    <?php foreach ($modules as $mod):
        if ($mod['webinterface'] != 1) continue;
        $allowedRoles = array_map('trim', explode(',', $mod['webroles']));
        if (!in_array($userRole, $allowedRoles)) continue;
    ?>
        <a href="<?= htmlspecialchars($mod['webpath']) ?>">
            <i class="fa-solid <?= htmlspecialchars($mod['webicon']) ?: 'fa-circle' ?>"></i> <?= htmlspecialchars($mod['webname']) ?>
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
            <a href="general.php" class="tab-link active">General</a>
            <a href="login.php" class="tab-link">Login</a>
            <a href="sip.php" class="tab-link">SIP</a>
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
    <div id="general" class="tab-content active">
        <div class="info-card login-settings">
            <form id="generalSettingsForm">
                <div class="info-row">
                    <span class="info-label">Show links to online documentation (docs.openpagingserver.org)</span>
                    <span>
                        <label class="switch">
                            <input type="checkbox" name="show_online_docs" id="docsToggle" <?= $show_online_docs === '1' ? 'checked' : '' ?>>
                            <span class="slider"></span>
                        </label>
                    </span>
                </div>
                <input type="hidden" name="save_general_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveGeneralBtn">Save Settings</button>
                    <span id="general-save-status"></span>
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
    const saveGeneralBtn = document.getElementById('saveGeneralBtn');
    const generalStatusText = document.getElementById('general-save-status');
    if (saveGeneralBtn) {
        saveGeneralBtn.addEventListener('click', function() {
            const formData = new FormData(document.getElementById('generalSettingsForm'));
            saveGeneralBtn.disabled = true;
            generalStatusText.innerText = "Saving...";
            fetch(window.location.href, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(response => {
                if (!response.ok) throw new Error('HTTP error ' + response.status);
                return response.json();
            })
            .then(data => {
                if(data.status === 'success') {
                    generalStatusText.innerText = "General settings saved.";
                    generalStatusText.style.color = "#4CAF50";
                } else {
                    generalStatusText.innerText = "Error saving settings.";
                    generalStatusText.style.color = "#F44336";
                }
            })
            .catch((e) => {
                console.error(e);
                generalStatusText.innerText = "Connection error.";
                generalStatusText.style.color = "#F44336";
            })
            .finally(() => {
                saveGeneralBtn.disabled = false;
                setTimeout(() => { generalStatusText.innerText = ""; }, 3000);
            });
        });
    }
});
</script>
</body>
</html>
