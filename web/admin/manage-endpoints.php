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
$endpointSuccess = trim((string)($_SESSION['endpoint_flash_success'] ?? ''));
unset($_SESSION['endpoint_flash_success']);


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

function sort_text($value) {
    return mb_strtolower(trim((string)$value), 'UTF-8');
}

function cmp_text($a, $b) {
    return strnatcasecmp((string)$a, (string)$b);
}

$sort = $_GET['sort'] ?? 'alpha';
$allowedSorts = ['alpha', 'module', 'devices'];
if (!in_array($sort, $allowedSorts, true)) {
    $sort = 'alpha';
}

[$endpointData, $endpointError] = endpoint_manager_request('LIST_ENDPOINTS');
$endpointWarning = $endpointData['warning'] ?? '';
$endpointModules = $endpointData['modules'] ?? [];
$endpointRows = [];
$endpointModuleErrors = [];

foreach ($endpointModules as $moduleInfo) {
    $moduleName = $moduleInfo['module'] ?? '';
    $displayName = $moduleInfo['display_name'] ?? $moduleName;
    $moduleCount = count($moduleInfo['endpoints'] ?? []);
    if (!empty($moduleInfo['error'])) {
        $endpointModuleErrors[] = trim($displayName . ': ' . $moduleInfo['error']);
    }
    foreach (($moduleInfo['endpoints'] ?? []) as $endpoint) {
        $endpointRows[] = [
            'module' => $moduleName,
            'module_display' => $displayName,
            'module_count' => $moduleCount,
            'id' => $endpoint['id'] ?? '',
            'name' => $endpoint['name'] ?? '',
            'model' => $endpoint['model'] ?? '',
            'address' => $endpoint['address'] ?? '',
            'status' => $endpoint['status'] ?? 'Unknown',
            'type' => $endpoint['type'] ?? '',
        ];
    }
}

usort($endpointRows, function ($a, $b) use ($sort) {
    $aModule = sort_text($a['module_display'] ?? '');
    $bModule = sort_text($b['module_display'] ?? '');
    $aName = sort_text(($a['name'] ?? '') ?: ($a['id'] ?? '') ?: ($a['address'] ?? ''));
    $bName = sort_text(($b['name'] ?? '') ?: ($b['id'] ?? '') ?: ($b['address'] ?? ''));
    $aAddress = sort_text($a['address'] ?? '');
    $bAddress = sort_text($b['address'] ?? '');
    $aCount = (int)($a['module_count'] ?? 0);
    $bCount = (int)($b['module_count'] ?? 0);

    if ($sort === 'module') {
        $moduleCmp = cmp_text($aModule, $bModule);
        if ($moduleCmp !== 0) {
            return $moduleCmp;
        }

        $nameCmp = cmp_text($aName, $bName);
        if ($nameCmp !== 0) {
            return $nameCmp;
        }

        return cmp_text($aAddress, $bAddress);
    }

    if ($sort === 'devices') {
        if ($aCount !== $bCount) {
            return $bCount <=> $aCount;
        }

        $moduleCmp = cmp_text($aModule, $bModule);
        if ($moduleCmp !== 0) {
            return $moduleCmp;
        }

        $nameCmp = cmp_text($aName, $bName);
        if ($nameCmp !== 0) {
            return $nameCmp;
        }

        return cmp_text($aAddress, $bAddress);
    }

    $nameCmp = cmp_text($aName, $bName);
    if ($nameCmp !== 0) {
        return $nameCmp;
    }

    $addressCmp = cmp_text($aAddress, $bAddress);
    if ($addressCmp !== 0) {
        return $addressCmp;
    }

    return cmp_text($aModule, $bModule);
});

$totalEndpoints = count($endpointRows);

function endpoint_display_line($endpoint) {
    $module = trim($endpoint['module_display'] ?? '');
    $type = trim($endpoint['type'] ?? '');
    $model = trim($endpoint['model'] ?? '');

    if (($endpoint['module'] ?? '') === 'siptrunks') {
        $label = $type !== '' ? $type : ($model !== '' ? $model : 'SIP Trunk');
        if ($label === 'SIP Trunk Extension') {
            return $label;
        }
        return $module !== '' ? trim($label . " ($module)") : $label;
    }

    $modelSuffix = $model;

    if ($module !== '' && stripos($modelSuffix, $module . ' ') === 0) {
        $modelSuffix = trim(substr($modelSuffix, strlen($module)));
    } elseif (strcasecmp($modelSuffix, $module) === 0) {
        $modelSuffix = '';
    }

    if ($module !== '' && stripos($type, $module) === 0) {
        $label = $type;
    } else {
        $label = trim($module . ' ' . $type);
    }

    if ($modelSuffix !== '' && stripos($label, $modelSuffix) === false) {
        $label = trim($label . ' ' . $modelSuffix);
    }

    if ($module !== '') {
        $label .= " ($module)";
    }

    return $label;
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Manage Endpoints - <?= htmlspecialchars($product_name) ?></title>
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
#content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; }
.header-actions { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; gap:16px; flex-wrap:wrap; }
.sort-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.sort-link { color:#1976D2; text-decoration:none; padding:8px 10px; border-radius:4px; border:1px solid #EEE; font-size:0.9em; }
.sort-link.active { background:#1976D2; color:#FFF; border-color:#1976D2; }
.settings-button { color:#1976D2; text-decoration:none; padding:9px 12px; border-radius:4px; border:1px solid #1976D2; font-size:0.9em; display:inline-flex; align-items:center; gap:7px; }
.settings-button:hover { background:rgba(25,118,210,0.08); }
.add-button { width:40px; height:40px; border-radius:50%; background:#1976D2; color:#FFF; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; box-shadow:0 2px 5px rgba(0,0,0,0.24); transition:background-color 0.2s, box-shadow 0.2s; }
.add-button:hover { background:#1565C0; box-shadow:0 4px 8px rgba(0,0,0,0.28); }
.info-card{ background:#FFF; padding:0; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; overflow:hidden; }
.summary-grid { display:grid; grid-template-columns:minmax(180px,280px); gap:12px; margin-bottom:16px; }
.summary-item { border:1px solid #EEE; border-radius:8px; padding:12px; background:#FFF; }
.summary-item strong { display:block; font-size:1.4em; font-weight:500; }
.muted { color:#777; font-size:0.9em; }
.endpoint-list { list-style:none; margin:0; padding:0; }
.endpoint-item { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:16px 18px; border-bottom:1px solid #EEE; background:#FFF; transition:background-color 0.2s, box-shadow 0.2s; }
.endpoint-item:last-child { border-bottom:none; }
.endpoint-item:hover { background:#F8F8F8; }
.endpoint-main { min-width:0; }
.endpoint-name { font-size:1.05em; font-weight:500; color:#202124; overflow-wrap:anywhere; }
.endpoint-meta { color:#555; margin-top:4px; }
.endpoint-status { display:flex; align-items:center; gap:7px; margin-top:6px; color:#666; font-size:0.92em; }
.status-dot { width:10px; height:10px; border-radius:50%; background:#9E9E9E; flex:0 0 10px; }
.status-dot.online, .status-dot.configured { background:#2E7D32; }
.status-dot.offline { background:#C62828; }
.status-dot.unchecked, .status-dot.unknown { background:#9E9E9E; }
.endpoint-actions { display:flex; align-items:center; gap:4px; flex:0 0 auto; }
.icon-action { width:36px; height:36px; border-radius:50%; color:#555; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; transition:background-color 0.2s, color 0.2s; }
.icon-action:hover { background:rgba(25,118,210,0.08); color:#1976D2; }
.icon-action.delete:hover { background:rgba(198,40,40,0.08); color:#C62828; }
.success { background:#E8F5E9; border:1px solid #A5D6A7; color:#1B5E20; padding:12px; border-radius:8px; margin-bottom:16px; }
.error { background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; padding:12px; border-radius:8px; margin-bottom:16px; }
@media(max-width:767px){ .endpoint-item{ align-items:flex-start; } .endpoint-actions{ flex-direction:column; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.info-card,.summary-item{ border:1px solid #333; background-color:#1E1E1E; }
.muted{ color:#BBB; }
.sort-link { color:#BB86FC; border-color:#333; }
.sort-link.active { background:#BB86FC; color:#000; border-color:#BB86FC; }
.settings-button { color:#BB86FC; border-color:#BB86FC; }
.settings-button:hover { background:rgba(187,134,252,0.1); }
.add-button { background:#BB86FC; color:#000; }
.add-button:hover { background:#A370F7; }
.endpoint-item { background:#1E1E1E; border-bottom:1px solid #333; }
.endpoint-item:hover { background:#242424; }
.endpoint-name { color:#EDEDED; }
.endpoint-meta { color:#CCC; }
.endpoint-status { color:#BBB; }
.icon-action { color:#BBB; }
.icon-action:hover { background:rgba(187,134,252,0.1); color:#BB86FC; }
.icon-action.delete:hover { background:rgba(244,67,54,0.12); color:#EF9A9A; }
.success { background:#14351A; border-color:#2E7D32; color:#C8E6C9; }
.error { background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
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
        <h1>Manage Endpoints</h1>
        <div class="sort-actions">
            <a class="add-button" href="/admin/new-endpoint.php" title="New Endpoint"><i class="fa-solid fa-plus"></i></a>
            <a class="settings-button" href="/admin/endpoint-module-settings.php"><i class="fa-solid fa-sliders"></i> Endpoint Module Settings</a>
            <span class="muted">Sort</span>
            <a class="sort-link <?= $sort === 'alpha' ? 'active' : '' ?>" href="?sort=alpha">Alphabetical</a>
            <a class="sort-link <?= $sort === 'module' ? 'active' : '' ?>" href="?sort=module">Module</a>
            <a class="sort-link <?= $sort === 'devices' ? 'active' : '' ?>" href="?sort=devices">Most Devices</a>
        </div>
    </div>

    <?php if ($endpointSuccess !== ''): ?>
        <div class="success"><?= htmlspecialchars($endpointSuccess) ?></div>
    <?php endif; ?>
    <?php if ($endpointError): ?>
        <div class="error"><?= htmlspecialchars($endpointError) ?></div>
    <?php endif; ?>
    <?php if ($endpointWarning): ?>
        <div class="error"><?= htmlspecialchars($endpointWarning) ?></div>
    <?php endif; ?>
    <?php foreach ($endpointModuleErrors as $moduleError): ?>
        <div class="error"><?= htmlspecialchars($moduleError) ?></div>
    <?php endforeach; ?>

    <div class="summary-grid">
        <div class="summary-item">
            <strong><?= htmlspecialchars((string)$totalEndpoints) ?></strong>
            <span class="muted">Endpoints</span>
        </div>
    </div>

    <div class="info-card">
        <?php if (empty($endpointRows)): ?>
            <p class="muted" style="text-align:center; padding:20px;">No endpoints found</p>
        <?php else: ?>
            <ul class="endpoint-list">
                <?php foreach ($endpointRows as $endpoint): ?>
                    <?php
                    $endpointStatus = trim((string)$endpoint['status']);
                    $statusToken = $endpointStatus !== '' ? strtok($endpointStatus, " (,") : '';
                    $statusClass = strtolower(preg_replace('/[^a-z0-9]+/i', '-', (string)$statusToken));
                    $endpointId = $endpoint['id'] ?: $endpoint['name'] ?: $endpoint['address'];
                    $query = http_build_query(['module' => $endpoint['module'], 'id' => $endpointId]);
                    ?>
                    <li class="endpoint-item">
                        <div class="endpoint-main">
                            <div class="endpoint-name"><?= htmlspecialchars($endpoint['name'] ?: $endpointId) ?></div>
                            <div class="endpoint-meta"><?= htmlspecialchars(endpoint_display_line($endpoint)) ?></div>
                            <?php if ($endpointStatus !== '' || !empty($endpoint['address'])): ?>
                                <div class="endpoint-status">
                                    <span class="status-dot <?= htmlspecialchars($statusClass) ?>"></span>
                                    <span><?= htmlspecialchars($endpointStatus) ?><?php if (!empty($endpoint['address'])): ?> (<?= htmlspecialchars($endpoint['address']) ?>)<?php endif; ?></span>
                                </div>
                            <?php endif; ?>
                        </div>
                        <div class="endpoint-actions">
                            <a class="icon-action" href="/admin/edit-endpoint.php?<?= htmlspecialchars($query) ?>" title="Edit"><i class="fa-solid fa-pen-to-square"></i></a>
                            <a class="icon-action delete" href="/admin/delete-endpoint.php?<?= htmlspecialchars($query) ?>" title="Delete"><i class="fa-solid fa-trash"></i></a>
                        </div>
                    </li>
                <?php endforeach; ?>
            </ul>
        <?php endif; ?>
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
