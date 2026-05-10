<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);

session_start();
require_once __DIR__ . '/../../config.php';
require_once __DIR__ . '/../../includes/sidebar-brand.php';

$is_insecure = (!isset($_SERVER['HTTPS']) || $_SERVER['HTTPS'] !== 'on');
if (isset($_SERVER['HTTP_X_FORWARDED_PROTO']) && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https') {
    $is_insecure = false;
}

if (!isset($_SESSION['user_id'])) {
    header("Location: /");
    exit;
}

$stmt = $pdo->prepare("SELECT role, adminperm FROM users WHERE id = :id LIMIT 1");
$stmt->execute(['id' => $_SESSION['user_id']]);
$userData = $stmt->fetch(PDO::FETCH_ASSOC);

$userRole = $userData['role'];
$isReceiver = ($userRole === 'receiver' || $userRole === 'tempreceiver');
if ($isReceiver) {
    header("Location: /dashboard.php");
    exit;
}
$adminPerms = explode(',', $userData['adminperm'] ?? '');
$adminPerms = array_map('trim', $adminPerms);

$hasPermission = (in_array('all', $adminPerms) || in_array('settings-branding', $adminPerms));
$isAdmin = ($userRole === 'admin' || $userRole === 'tempadmin');

if (!$hasPermission) {
    http_response_code(403);
    header('Content-Type: text/html; charset=UTF-8');
    readfile('/var/www/html/.errors/403.html');
    exit;
}

$username = $_SESSION['username'] ?? 'User';


$stmt = $pdo->query("SELECT parameter, value FROM systemsettings");
$settings = [];
foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
    $settings[$row['parameter']] = $row['value'];
}

$product_name = $settings['product_name'] ?? 'Open Paging Server';
$favicon = $settings['favicon'] ?? '';
$show_online_docs = $settings['show_online_docs'] ?? '1';
$use_logo_in_sidebar = $settings['use_logo_in_sidebar'] ?? '1';
$sidebar_logo_light = $settings['sidebar_logo_light'] ?? '/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png';
$sidebar_logo_dark = $settings['sidebar_logo_dark'] ?? '/assets/OPENPAGINGSERVER-768x576-DARKMODE.png';

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['save_branding_settings'])) {
    $new_product_name = $_POST['product_name'] ?? 'Open Paging Server';
    $new_use_logo_in_sidebar = isset($_POST['use_logo_in_sidebar']) ? '1' : '0';
    $new_sidebar_logo_light = trim((string)($_POST['sidebar_logo_light'] ?? '/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png'));
    $new_sidebar_logo_dark = trim((string)($_POST['sidebar_logo_dark'] ?? '/assets/OPENPAGINGSERVER-768x576-DARKMODE.png'));

    $upsert = $pdo->prepare("
        INSERT INTO systemsettings (`parameter`, `value`, `description`)
        VALUES (:parameter, :value, :description)
        ON DUPLICATE KEY UPDATE `value` = VALUES(`value`), `description` = VALUES(`description`)
    ");
    $upsert->execute([
        'parameter' => 'product_name',
        'value' => $new_product_name,
        'description' => 'Name of this server.',
    ]);
    $upsert->execute([
        'parameter' => 'use_logo_in_sidebar',
        'value' => $new_use_logo_in_sidebar,
        'description' => 'Use a logo in the sidebar, if disabled the product name will show',
    ]);
    $upsert->execute([
        'parameter' => 'sidebar_logo_light',
        'value' => $new_sidebar_logo_light,
        'description' => 'Light mode logo for the sidebar',
    ]);
    $upsert->execute([
        'parameter' => 'sidebar_logo_dark',
        'value' => $new_sidebar_logo_dark,
        'description' => 'Dark mode logo for the sidebar',
    ]);

    if (isset($_SERVER['HTTP_X_REQUESTED_WITH']) && $_SERVER['HTTP_X_REQUESTED_WITH'] === 'XMLHttpRequest') {
        echo json_encode(['status' => 'success']);
        exit;
    }
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Branding - <?= htmlspecialchars($product_name) ?></title>
<?php if (!empty($favicon)): ?>
<link rel="icon" href="<?= htmlspecialchars($favicon) ?>" type="image/x-icon">
<?php endif; ?>
<link href="https://fonts.googleapis.com/css?family=Roboto:300,400,500,700&display=swap" rel="stylesheet" />
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet" />
<link href="/assets/sidebar-brand.css" rel="stylesheet" />
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
.logout-btn-mobile{ background-color:#C62828; border:none; cursor:pointer; transition:background-color 0.3s; display:none; }
#mobile-header{ display:flex; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h2{ color:#1976D2; margin-bottom:16px; font-weight:300; }
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
@media(max-width:767px){ .tabs-desktop { display: none; } .tabs-mobile { display: block; } .logout-btn{ display:none; } .logout-btn-mobile{ display:block; } }
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
h2,h3{ color:#BB86FC; }
h4 { color:#BB86FC; }
.info-label { color:#BBB; }
.info-row { border-bottom:1px solid #333; }
.tabs-container { border-bottom-color: #333; }
.tab-link { background: #333; color: #BBB; }
.tab-link.active { background: #BB86FC; color: #000; }
.tabs-mobile { background: #1E1E1E; color: #E0E0E0; border-color: #444; }
}
.login-settings h4 { margin: 0 0 4px 0; font-weight: 500; font-size: 1.1em; }
.login-settings p { margin: 0 0 12px 0; font-size: 0.9em; color: #666; }
@media(prefers-color-scheme:dark){ .login-settings p { color: #AAA; } }
.login-settings input[type="text"] { width:100%; padding:10px; border-radius:6px; border:1px solid #CCC; font-family:inherit; font-size:14px; box-sizing:border-box; }
.login-settings button { background:#1976D2; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; }
@media(prefers-color-scheme:dark){
.login-settings input[type="text"] { background:#1E1E1E; border:1px solid #444; color:#E0E0E0; }
.login-settings button { background:#BB86FC; color:#000; }
}
#branding-save-status { margin-left: 10px; font-size: 0.85em; transition: opacity 0.5s; }
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
    <a href="/paging"><i class="fa-solid fa-bullhorn"></i> Paging</a>
    <a href="/messages"><i class="fa-solid fa-message"></i> Messages</a>
    <a href="/history"><i class="fa-solid fa-clock-rotate-left"></i> History</a>
    <a href="/bells"><i class="fa-solid fa-bell"></i> Bells</a>
    <a href="/assets/"><i class="fa-solid fa-folder-open"></i> Assets</a>

    <?php if ($isAdmin): ?>
      <a href="/admin/manage-users.php" class="admin-only"><i class="fa-solid fa-users-cog"></i> Manage Users</a>
      <a href="/admin/manage-endpoints.php" class="admin-only"><i class="fa-solid fa-shapes"></i> Manage Endpoints</a>
      <a href="/admin/manage-groups.php"><i class="fa-solid fa-user-group"></i> Manage Groups</a>
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
            <a href="sip.php" class="tab-link">SIP</a>
            <a href="branding.php" class="tab-link active">Branding</a>
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

    <div class="tab-content active">
        <div class="info-card login-settings">
            <form id="brandingSettingsForm">
                <div style="margin-bottom:16px;">
                    <h4>Product Name</h4>
                    <p>This displays throughout the various user interfaces of Open Paging Server. You can set a name to relfect your facility.</p>
                    <input type="text" name="product_name" value="<?= htmlspecialchars($product_name) ?>">
                </div>

                <div style="margin-bottom:16px;">
                    <h4>Sidebar Logo</h4>
                    <p>When enabled, the sidebar uses the configured logo paths instead of the product name.</p>
                    <label style="display:flex; gap:8px; align-items:center; margin-bottom:12px;">
                        <input type="checkbox" name="use_logo_in_sidebar" value="1" <?= ops_sidebar_truthy($use_logo_in_sidebar) ? 'checked' : '' ?> style="width:auto;">
                        <span>Use logo in sidebar</span>
                    </label>
                    <div style="display:grid; gap:12px;">
                        <div>
                            <h4 style="font-size:1em;">Light Mode Sidebar Logo</h4>
                            <input type="text" name="sidebar_logo_light" value="<?= htmlspecialchars($sidebar_logo_light) ?>">
                        </div>
                        <div>
                            <h4 style="font-size:1em;">Dark Mode Sidebar Logo</h4>
                            <input type="text" name="sidebar_logo_dark" value="<?= htmlspecialchars($sidebar_logo_dark) ?>">
                        </div>
                    </div>
                </div>

                <input type="hidden" name="save_branding_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveBrandingBtn">Save Branding Settings</button>
                    <span id="branding-save-status"></span>
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
    const saveBrandingBtn = document.getElementById('saveBrandingBtn');
    const brandingStatusText = document.getElementById('branding-save-status');

    if(saveBrandingBtn){
        saveBrandingBtn.addEventListener('click', function() {
            const formData = new FormData(document.getElementById('brandingSettingsForm'));
            saveBrandingBtn.disabled = true;
            brandingStatusText.innerText = "Saving...";
            brandingStatusText.style.color = "inherit";

            fetch(window.location.href, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(response => response.json())
            .then(data => {
                if(data.status === 'success') {
                    brandingStatusText.innerText = "Branding saved.";
                    brandingStatusText.style.color = "#4CAF50";
                    setTimeout(() => { location.reload(); }, 1000);
                } else {
                    brandingStatusText.innerText = "Error saving.";
                    brandingStatusText.style.color = "#F44336";
                }
            })
            .catch(error => {
                brandingStatusText.innerText = "Connection error.";
                brandingStatusText.style.color = "#F44336";
            })
            .finally(() => {
                saveBrandingBtn.disabled = false;
                setTimeout(() => { brandingStatusText.innerText = ""; }, 3000);
            });
        });
    }
});
</script>
</body>
</html>
