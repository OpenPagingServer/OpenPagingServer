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
$isReceiver = ($userRole === 'receiver' || $userRole === 'tempreceiver');
if ($isReceiver) {
    header("Location: /dashboard.php");
    exit;
}
$isAdmin = ($userRole === 'admin' || $userRole === 'tempadmin');

if (!$isAdmin) {
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
$modulesRoot = realpath(__DIR__ . '/../../endpoint-modules');

function safe_name($value) {
    return preg_match('/^[A-Za-z0-9_-]+$/', (string)$value) === 1;
}

function xml_text($root, $tag, $default = '') {
    $node = $root->{$tag} ?? null;
    return $node !== null ? trim((string)$node) : $default;
}

function module_info($moduleDir, $module) {
    $info = [
        'module' => $module,
        'name' => $module,
        'description' => '',
        'input_type' => 'Output',
        'author' => '',
        'created' => '',
        'updated' => '',
        'version' => '',
    ];
    $infoPath = $moduleDir . DIRECTORY_SEPARATOR . 'info.xml';
    if (!is_file($infoPath)) {
        return $info;
    }
    $root = @simplexml_load_file($infoPath);
    if ($root === false) {
        return $info;
    }
    $info['name'] = xml_text($root, 'name', $module) ?: $module;
    $info['description'] = xml_text($root, 'desp') ?: xml_text($root, 'description');
    $info['input_type'] = xml_text($root, 'type', 'Output') ?: 'Output';
    $info['author'] = xml_text($root, 'author');
    $info['created'] = xml_text($root, 'created') ?: xml_text($root, 'datecreated');
    $info['updated'] = xml_text($root, 'updated');
    $info['version'] = xml_text($root, 'version');
    return $info;
}

function discover_endpoint_modules($modulesRoot) {
    if (!$modulesRoot || !is_dir($modulesRoot)) {
        return [];
    }
    $modules = [];
    foreach (scandir($modulesRoot) as $module) {
        if ($module === '.' || $module === '..' || !safe_name($module)) {
            continue;
        }
        $moduleDir = $modulesRoot . DIRECTORY_SEPARATOR . $module;
        if (!is_dir($moduleDir . DIRECTORY_SEPARATOR . 'endpoint-forms') || !is_file($moduleDir . DIRECTORY_SEPARATOR . 'endpoint-forms' . DIRECTORY_SEPARATOR . 'forms.php')) {
            continue;
        }
        $modules[] = module_info($moduleDir, $module);
    }
    usort($modules, function ($a, $b) {
        return strtolower($a['name']) <=> strtolower($b['name']);
    });
    return $modules;
}

$endpointModules = discover_endpoint_modules($modulesRoot);
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>New Endpoint - <?= htmlspecialchars($product_name) ?></title>
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
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:24px; min-height:100vh; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; }
.header-actions { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; gap:16px; flex-wrap:wrap; }
.back-link { color:#1976D2; text-decoration:none; display:inline-flex; align-items:center; gap:8px; }
.module-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:12px; }
.module-card { background:#FFF; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); padding:16px; display:flex; flex-direction:column; gap:8px; text-decoration:none; color:inherit; min-height:140px; }
.module-card:focus,.module-card:hover { border-color:#1976D2; box-shadow:0 0 0 2px rgba(25,118,210,0.15); outline:none; }
.module-name { font-size:1.12em; font-weight:500; color:#202124; }
.module-type { align-self:flex-start; background:#E3F2FD; color:#1565C0; border-radius:16px; padding:4px 10px; font-size:0.85em; }
.module-description { color:#444; line-height:1.4; flex:1; }
.module-meta { color:#666; font-size:0.9em; line-height:1.35; }
.muted { color:#777; font-size:0.9em; }
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
.module-card{ border:1px solid #333; background-color:#1E1E1E; }
.module-card:focus,.module-card:hover { border-color:#BB86FC; box-shadow:0 0 0 2px rgba(187,134,252,0.18); }
.module-name { color:#EDEDED; }
.module-description,.module-meta,.muted{ color:#BBB; }
.module-type { background:#2A2433; color:#BB86FC; }
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
        <h1>New Endpoint</h1>
        <a class="back-link" href="/admin/manage-endpoints.php"><i class="fa-solid fa-arrow-left"></i> Back</a>
    </div>

    <?php if (empty($endpointModules)): ?>
        <p class="muted">No endpoint modules with add forms were found.</p>
    <?php else: ?>
        <div class="module-grid">
            <?php foreach ($endpointModules as $module): ?>
                <a class="module-card" href="/admin/new-endpoint-configure.php?module=<?= urlencode($module['module']) ?>">
                    <div class="module-name"><?= htmlspecialchars($module['name']) ?></div>
                    <?php if ($module['author'] !== '' || $module['created'] !== '' || $module['updated'] !== '' || $module['version'] !== ''): ?>
                        <div class="module-meta">
                            <?php if ($module['author'] !== ''): ?>By <?= htmlspecialchars($module['author']) ?><?php endif; ?>
                            <?php if ($module['version'] !== ''): ?><?= $module['author'] !== '' ? ' - ' : '' ?>Version <?= htmlspecialchars($module['version']) ?><?php endif; ?>
                            <?php if ($module['created'] !== ''): ?><?= ($module['author'] !== '' || $module['version'] !== '') ? ' - ' : '' ?>Created <?= htmlspecialchars($module['created']) ?><?php endif; ?>
                            <?php if ($module['updated'] !== ''): ?><?= ($module['author'] !== '' || $module['version'] !== '' || $module['created'] !== '') ? ' - ' : '' ?>Updated <?= htmlspecialchars($module['updated']) ?><?php endif; ?>
                        </div>
                    <?php endif; ?>
                    <?php if ($module['description'] !== ''): ?>
                        <div class="module-description"><?= htmlspecialchars($module['description']) ?></div>
                    <?php endif; ?>
                    <div class="module-type"><?= htmlspecialchars($module['input_type']) ?></div>
                </a>
            <?php endforeach; ?>
        </div>
    <?php endif; ?>
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
