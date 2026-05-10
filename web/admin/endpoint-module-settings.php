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
if ($userRole !== 'admin' && $userRole !== 'tempadmin') {
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
$builtinEndpointModules = ['siptrunks' => true];

function h($value) {
    return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8');
}

function safe_name($value) {
    return preg_match('/^[A-Za-z0-9_-]+$/', (string)$value) === 1;
}

function xml_text($root, $tag, $default = '') {
    $node = $root->{$tag} ?? null;
    return $node !== null ? trim((string)$node) : $default;
}

function module_info($moduleDir) {
    $module = basename($moduleDir);
    $info = [
        'module' => $module,
        'name' => $module,
        'author' => '',
        'description' => '',
        'input_type' => 'Output',
        'version' => '',
        'updated' => '',
        'has_settings_page' => is_file($moduleDir . DIRECTORY_SEPARATOR . 'settings.php'),
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
    $info['author'] = xml_text($root, 'author');
    $info['description'] = xml_text($root, 'desp') ?: xml_text($root, 'description');
    $info['input_type'] = xml_text($root, 'type', 'Output') ?: 'Output';
    $info['version'] = xml_text($root, 'version');
    $info['updated'] = xml_text($root, 'updated');
    return $info;
}

function discover_modules($modulesRoot) {
    if (!$modulesRoot || !is_dir($modulesRoot)) {
        return [];
    }
    $modules = [];
    foreach (scandir($modulesRoot) as $entry) {
        if ($entry === '.' || $entry === '..' || !safe_name($entry)) {
            continue;
        }
        $moduleDir = $modulesRoot . DIRECTORY_SEPARATOR . $entry;
        if (!is_dir($moduleDir) || !is_file($moduleDir . DIRECTORY_SEPARATOR . 'info.xml')) {
            continue;
        }
        $modules[$entry] = module_info($moduleDir);
    }
    uasort($modules, function ($a, $b) {
        return [strtolower($a['name']), strtolower($a['module'])] <=> [strtolower($b['name']), strtolower($b['module'])];
    });
    return $modules;
}

function endpointmodulesloaded_columns($pdo) {
    $columns = [];
    try {
        $stmt = $pdo->query("SHOW COLUMNS FROM endpointmodulesloaded");
        foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
            $columns[$row['Field']] = true;
        }
    } catch (Throwable $exc) {
        return [];
    }
    return $columns;
}

function ensure_endpointmodulesloaded_table($pdo) {
    $pdo->exec("CREATE TABLE IF NOT EXISTS endpointmodulesloaded (`dir` VARCHAR(100) NOT NULL, enabled VARCHAR(10) NOT NULL DEFAULT 'true', PRIMARY KEY (`dir`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci");
}

function normalize_endpointmodulesloaded($pdo, $modules, $postedEnabled = null) {
    global $builtinEndpointModules;
    ensure_endpointmodulesloaded_table($pdo);
    $columns = endpointmodulesloaded_columns($pdo);
    $selectName = isset($columns['name']) ? ', `name`' : '';
    $stmt = $pdo->query("SELECT `dir`, enabled$selectName FROM endpointmodulesloaded");
    $states = [];
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $dir = trim((string)($row['dir'] ?? ''));
        if ($dir === '' || !safe_name($dir)) {
            continue;
        }
        if (!isset($states[$dir])) {
            $states[$dir] = ['enabled' => 'false', 'name' => $row['name'] ?? ''];
        }
        if (strtolower((string)($row['enabled'] ?? '')) === 'true') {
            $states[$dir]['enabled'] = 'true';
        }
        if (($states[$dir]['name'] ?? '') === '' && isset($row['name'])) {
            $states[$dir]['name'] = $row['name'];
        }
    }

    foreach ($modules as $module => $moduleInfo) {
        if (!isset($states[$module])) {
            $states[$module] = ['enabled' => 'false', 'name' => $moduleInfo['name'] ?? $module];
        }
        if (is_array($postedEnabled)) {
            $states[$module]['enabled'] = isset($postedEnabled[$module]) ? 'true' : 'false';
        }
        if (isset($builtinEndpointModules[$module])) {
            $states[$module]['enabled'] = 'true';
        }
        if (($states[$module]['name'] ?? '') === '') {
            $states[$module]['name'] = $moduleInfo['name'] ?? $module;
        }
    }

    $pdo->beginTransaction();
    try {
        $pdo->exec("DELETE FROM endpointmodulesloaded");
        $columns = endpointmodulesloaded_columns($pdo);
        $insert = isset($columns['name'])
            ? $pdo->prepare("INSERT INTO endpointmodulesloaded (`dir`, enabled, `name`) VALUES (:dir, :enabled, :name)")
            : $pdo->prepare("INSERT INTO endpointmodulesloaded (`dir`, enabled) VALUES (:dir, :enabled)");
        foreach ($states as $dir => $state) {
            if (!safe_name($dir)) {
                continue;
            }
            $params = ['dir' => $dir, 'enabled' => strtolower((string)$state['enabled']) === 'true' ? 'true' : 'false'];
            if (isset($columns['name'])) {
                $params['name'] = $state['name'] ?? '';
            }
            $insert->execute($params);
        }
        $pdo->commit();
    } catch (Throwable $exc) {
        if ($pdo->inTransaction()) {
            $pdo->rollBack();
        }
        throw $exc;
    }

    try {
        $pdo->exec("ALTER TABLE endpointmodulesloaded MODIFY `dir` VARCHAR(100) NOT NULL");
    } catch (Throwable $exc) {
    }
    try {
        $pdo->exec("ALTER TABLE endpointmodulesloaded ADD PRIMARY KEY (`dir`)");
    } catch (Throwable $exc) {
    }
}

function endpointmodulesloaded_states($pdo) {
    $states = [];
    $stmt = $pdo->query("SELECT `dir`, enabled FROM endpointmodulesloaded");
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $states[$row['dir']] = strtolower((string)$row['enabled']) === 'true';
    }
    return $states;
}

$modules = discover_modules($modulesRoot);
$visibleModules = array_filter(
    $modules,
    function ($module) use ($builtinEndpointModules) {
        return !isset($builtinEndpointModules[$module]);
    },
    ARRAY_FILTER_USE_KEY
);
$messages = [];
$errors = [];

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    try {
        $action = $_POST['action'] ?? '';
        $module = $_POST['module'] ?? '';
        if ($action !== 'toggle_module' || !safe_name($module) || !isset($visibleModules[$module])) {
            throw new RuntimeException('Invalid module action.');
        }
        normalize_endpointmodulesloaded($pdo, $modules);
        $currentStates = endpointmodulesloaded_states($pdo);
        $postedEnabled = [];
        foreach ($modules as $moduleKey => $_moduleInfo) {
            if (!empty($currentStates[$moduleKey])) {
                $postedEnabled[$moduleKey] = '1';
            }
        }
        if (!empty($currentStates[$module])) {
            unset($postedEnabled[$module]);
            $messages[] = $modules[$module]['name'] . ' disabled.';
        } else {
            $postedEnabled[$module] = '1';
            $messages[] = $modules[$module]['name'] . ' enabled.';
        }
        normalize_endpointmodulesloaded($pdo, $modules, $postedEnabled);
    } catch (Throwable $exc) {
        $errors[] = $exc->getMessage();
    }
}

$enabledRows = [];
try {
    normalize_endpointmodulesloaded($pdo, $modules);
    $stmt = $pdo->query("SELECT `dir`, enabled FROM endpointmodulesloaded");
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $enabledRows[$row['dir']] = strtolower((string)$row['enabled']) === 'true';
    }
} catch (Throwable $exc) {
    $errors[] = $exc->getMessage();
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Endpoint Module Settings - <?= h($product_name) ?></title>
<?php if (!empty($favicon)): ?>
<link rel="icon" href="<?= h($favicon) ?>" type="image/x-icon">
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
.module-card{ background:#FFF; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; overflow:hidden; }
.module-head{ display:flex; justify-content:space-between; gap:18px; padding:18px; border-bottom:1px solid #EEE; align-items:flex-start; }
.module-title{ font-size:1.1em; font-weight:500; color:#202124; }
.module-meta{ color:#666; margin-top:5px; line-height:1.4; }
.module-controls{ display:flex; align-items:center; justify-content:flex-end; gap:10px; flex-wrap:wrap; min-width:220px; }
.muted{ color:#777; font-size:0.9em; padding:18px; display:block; }
.success{ background:#E8F5E9; border:1px solid #A5D6A7; color:#1B5E20; padding:12px; border-radius:8px; margin-bottom:16px; }
.error{ background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; padding:12px; border-radius:8px; margin-bottom:16px; }
.module-settings-button,.toggle-button{ background:transparent; color:#1976D2; border:1px solid #1976D2; border-radius:4px; padding:9px 12px; cursor:pointer; font:inherit; display:inline-flex; align-items:center; gap:7px; text-decoration:none; min-height:38px; box-sizing:border-box; }
.module-settings-button:hover{ background:rgba(25,118,210,0.08); }
.toggle-button.enabled{ color:#C62828; border-color:#C62828; }
.toggle-button.enabled:hover{ background:rgba(198,40,40,0.08); }
.toggle-button.disabled:hover{ background:rgba(25,118,210,0.08); }
@media(max-width:767px){ .module-head{ display:grid; grid-template-columns:1fr; } .module-controls{ justify-content:flex-start; min-width:0; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.back-link{ color:#BB86FC; }
.module-card{ border-color:#333; background:#1E1E1E; }
.module-head{ border-bottom-color:#333; }
.module-title{ color:#EDEDED; }
.module-meta,.muted{ color:#BBB; }
.module-settings-button,.toggle-button.disabled{ color:#BB86FC; border-color:#BB86FC; }
.module-settings-button:hover{ background:rgba(187,134,252,0.1); }
.toggle-button.enabled{ color:#EF9A9A; border-color:#EF9A9A; }
.toggle-button.enabled:hover{ background:rgba(244,67,54,0.12); }
.toggle-button.disabled:hover{ background:rgba(187,134,252,0.1); }
.success{ background:#14351A; border-color:#2E7D32; color:#C8E6C9; }
.error{ background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
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
        <h1>Endpoint Module Settings</h1>
        <a class="back-link" href="/admin/manage-endpoints.php"><i class="fa-solid fa-arrow-left"></i> Back</a>
    </div>

    <?php foreach ($messages as $message): ?><div class="success"><?= h($message) ?></div><?php endforeach; ?>
    <?php foreach ($errors as $error): ?><div class="error"><?= h($error) ?></div><?php endforeach; ?>

    <?php if (empty($visibleModules)): ?>
        <p class="muted">No endpoint modules found.</p>
    <?php endif; ?>
    <?php foreach ($visibleModules as $module => $moduleInfo): ?>
        <?php $isEnabled = $enabledRows[$module] ?? false; ?>
        <section class="module-card">
            <div class="module-head">
                <div>
                    <div class="module-title"><?= h($moduleInfo['name']) ?></div>
                    <div class="module-meta">
                        <?= h($module) ?><?php if ($moduleInfo['input_type']): ?> - <?= h($moduleInfo['input_type']) ?><?php endif; ?>
                        <?php if ($moduleInfo['version']): ?> - Version <?= h($moduleInfo['version']) ?><?php endif; ?>
                    </div>
                    <?php if ($moduleInfo['description']): ?><div class="module-meta"><?= h($moduleInfo['description']) ?></div><?php endif; ?>
                </div>
                <div class="module-controls">
                    <?php if ($moduleInfo['has_settings_page']): ?>
                        <a class="module-settings-button" href="/admin/endpoint-module-settings-configure.php?module=<?= urlencode($module) ?>">
                            <i class="fa-solid fa-sliders"></i> Module Settings
                        </a>
                    <?php endif; ?>
                    <form method="post">
                        <input type="hidden" name="action" value="toggle_module">
                        <input type="hidden" name="module" value="<?= h($module) ?>">
                        <button class="toggle-button <?= $isEnabled ? 'enabled' : 'disabled' ?>" type="submit">
                            <i class="fa-solid <?= $isEnabled ? 'fa-toggle-on' : 'fa-toggle-off' ?>"></i>
                            <?= $isEnabled ? 'Disable' : 'Enable' ?>
                        </button>
                    </form>
                </div>
            </div>
        </section>
    <?php endforeach; ?>
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
