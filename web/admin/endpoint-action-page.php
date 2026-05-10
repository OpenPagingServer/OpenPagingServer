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

$stmt = $pdo->prepare("SELECT role FROM users WHERE id = :id LIMIT 1");
$stmt->execute(['id' => $_SESSION['user_id']]);
$userRole = $stmt->fetchColumn();
if ($userRole === 'receiver' || $userRole === 'tempreceiver') {
    header("Location: /dashboard.php");
    exit;
}
if (!in_array($userRole, ['admin', 'tempadmin'], true)) {
    http_response_code(403);
    echo "Forbidden";
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
$endpointAction = $endpointAction ?? '';

function endpoint_action_safe_name($value) {
    return preg_match('/^[A-Za-z0-9_-]+$/', (string)$value) === 1;
}

function endpoint_action_xml_text($root, $tag, $default = '') {
    $node = $root->{$tag} ?? null;
    return $node !== null ? trim((string)$node) : $default;
}

function endpoint_action_module_info($moduleDir, $module) {
    $info = ['module' => $module, 'name' => $module, 'description' => '', 'input_type' => 'Output'];
    $infoPath = $moduleDir . DIRECTORY_SEPARATOR . 'info.xml';
    if (!is_file($infoPath)) {
        return $info;
    }
    $root = @simplexml_load_file($infoPath);
    if ($root === false) {
        return $info;
    }
    $info['name'] = endpoint_action_xml_text($root, 'name', $module) ?: $module;
    $info['description'] = endpoint_action_xml_text($root, 'desp') ?: endpoint_action_xml_text($root, 'description');
    $info['input_type'] = endpoint_action_xml_text($root, 'type', 'Output') ?: 'Output';
    return $info;
}

if (!in_array($endpointAction, ['edit', 'delete'], true)) {
    http_response_code(400);
    echo "Invalid endpoint action";
    exit;
}

$module = $_GET['module'] ?? '';
$endpointId = trim((string)($_GET['id'] ?? ''));
$modulesRoot = realpath(__DIR__ . '/../../endpoint-modules');
$moduleDir = $modulesRoot && endpoint_action_safe_name($module) ? realpath($modulesRoot . DIRECTORY_SEPARATOR . $module) : false;
$actionFile = $endpointAction . '.php';

if ($endpointId === '' || strlen($endpointId) > 255) {
    http_response_code(400);
    echo "Invalid endpoint";
    exit;
}

if (!$modulesRoot || !$moduleDir || strpos($moduleDir, $modulesRoot) !== 0 || !is_file($moduleDir . DIRECTORY_SEPARATOR . $actionFile)) {
    http_response_code(404);
    echo "Endpoint module action not found";
    exit;
}

$moduleInfo = endpoint_action_module_info($moduleDir, $module);
$actionTitle = $endpointAction === 'delete' ? 'Delete Endpoint' : 'Edit Endpoint';
$frameSrc = '/admin/endpoint-action-frame.php?' . http_build_query([
    'action' => $endpointAction,
    'module' => $module,
    'id' => $endpointId,
]);
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title><?= htmlspecialchars($actionTitle) ?> - <?= htmlspecialchars($product_name) ?></title>
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
#mobile-header{ display:flex; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; min-height:100vh; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; margin-bottom:4px; }
.header-actions { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:20px; gap:16px; flex-wrap:wrap; }
.back-link { color:#1976D2; text-decoration:none; display:inline-flex; align-items:center; gap:8px; margin-top:8px; }
.muted { color:#666; margin-top:0; }
.endpoint-id { color:#555; font-size:0.92em; overflow-wrap:anywhere; }
.frame-shell { background:#FFF; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); padding:12px; box-sizing:border-box; }
.form-frame { width:100%; min-height:620px; border:0; border-radius:6px; background:#FFF; box-sizing:border-box; display:block; }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.back-link { color:#BB86FC; }
.muted,.endpoint-id{ color:#BBB; }
.frame-shell { border-color:#333; background:#1E1E1E; box-shadow:none; }
.form-frame { background:#1E1E1E; }
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
    <a href="/paging"><i class="fa-solid fa-bullhorn"></i> Paging</a>
    <a href="/messages"><i class="fa-solid fa-message"></i> Messages</a>
    <a href="/history"><i class="fa-solid fa-clock-rotate-left"></i> History</a>
    <a href="/bells"><i class="fa-solid fa-bell"></i> Bells</a>
    <a href="/assets/"><i class="fa-solid fa-folder-open"></i> Assets</a>
    <a href="/admin/manage-users.php" class="admin-only"><i class="fa-solid fa-users-cog"></i> Manage Users</a>
    <a href="/admin/manage-endpoints.php" class="active admin-only"><i class="fa-solid fa-shapes"></i> Manage Endpoints</a>
    <a href="/admin/manage-groups.php" class="admin-only"><i class="fa-solid fa-user-group"></i> Manage Groups</a>
    <a href="/admin/settings/general.php" class="admin-only"><i class="fa-solid fa-cogs"></i> Server Settings</a>
    <?php if ($show_online_docs == '1'): ?>
    <a href="https://docs.openpagingserver.org"><i class="fa-solid fa-book"></i> Online Documentation</a>
    <?php endif; ?>
    <button class="logout-btn-mobile" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
    <button class="logout-btn" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
</div>
<div id="content" onclick="closeSidebarOnContentClick()">
    <div class="header-actions">
        <div>
            <h1><?= htmlspecialchars($actionTitle) ?></h1>
            <p class="muted"><?= htmlspecialchars($moduleInfo['name']) ?></p>
            <div class="endpoint-id"><?= htmlspecialchars($endpointId) ?></div>
        </div>
        <a class="back-link" href="/admin/manage-endpoints.php"><i class="fa-solid fa-arrow-left"></i> Endpoints</a>
    </div>
    <div class="frame-shell">
        <iframe class="form-frame" sandbox="allow-forms allow-same-origin allow-scripts allow-top-navigation" src="<?= htmlspecialchars($frameSrc) ?>" title="<?= htmlspecialchars($actionTitle) ?>"></iframe>
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
</script>
</body>
</html>
