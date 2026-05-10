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

function endpoint_label($endpoint, $moduleDisplay) {
    $name = trim($endpoint['name'] ?? $endpoint['id'] ?? '');
    $model = trim($endpoint['model'] ?? '');
    $type = trim($endpoint['type'] ?? '');
    $address = trim($endpoint['address'] ?? '');
    $parts = array_filter([$model, $type, $address]);
    return $name . (empty($parts) ? '' : ' - ' . implode(' · ', $parts)) . ' (' . $moduleDisplay . ')';
}

function next_group_id($pdo) {
    $stmt = $pdo->query("SELECT id FROM groups");
    $used = [];
    foreach ($stmt->fetchAll(PDO::FETCH_COLUMN) as $id) {
        if (preg_match('/^\d+$/', (string)$id)) {
            $used[(int)$id] = true;
        }
    }
    $next = 1;
    while (isset($used[$next])) {
        $next++;
    }
    return (string)$next;
}

[$endpointData, $endpointError] = endpoint_manager_request('LIST_ENDPOINTS');
$endpointModules = $endpointData['modules'] ?? [];
$endpointWarning = $endpointData['warning'] ?? '';
$outputEndpoints = [];
$endpointModuleErrors = [];

foreach ($endpointModules as $moduleInfo) {
    $moduleName = $moduleInfo['module'] ?? '';
    $displayName = $moduleInfo['display_name'] ?? $moduleName;
    if (!empty($moduleInfo['error'])) {
        $endpointModuleErrors[] = trim($displayName . ': ' . $moduleInfo['error']);
    }
    foreach (($moduleInfo['endpoints'] ?? []) as $endpoint) {
        if (!output_capable($endpoint)) {
            continue;
        }
        $endpointId = trim($endpoint['id'] ?? '');
        if ($moduleName === '' || $endpointId === '') {
            continue;
        }
        $value = $moduleName . '/' . $endpointId;
        $outputEndpoints[] = [
            'value' => $value,
            'label' => endpoint_label($endpoint, $displayName),
        ];
    }
}

usort($outputEndpoints, function ($a, $b) {
    return strtolower($a['label']) <=> strtolower($b['label']);
});
$allowedMembers = array_column($outputEndpoints, 'value');

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = $_POST['action'] ?? '';
    if ($action === 'delete') {
        $groupId = trim($_POST['group_id'] ?? '');
        if ($groupId !== '') {
            $stmt = $pdo->prepare("DELETE FROM groups WHERE id = :id");
            $stmt->execute(['id' => $groupId]);
        }
        header("Location: /admin/manage-groups.php");
        exit;
    }

    if ($action === 'save') {
        $groupId = trim($_POST['group_id'] ?? '');
        $groupName = trim($_POST['name'] ?? '');
        $members = $_POST['members'] ?? [];
        if (!is_array($members)) {
            $members = [];
        }
        $members = array_values(array_unique(array_filter(array_map('trim', $members))));
        $members = array_values(array_intersect($members, $allowedMembers));
        $membersValue = implode(',', $members);

        if ($groupName !== '') {
            if ($groupId === '') {
                $groupId = next_group_id($pdo);
                $stmt = $pdo->prepare("INSERT INTO groups (id, name, members) VALUES (:id, :name, :members)");
            } else {
                $stmt = $pdo->prepare("UPDATE groups SET name = :name, members = :members WHERE id = :id");
            }
            $stmt->execute([
                'id' => $groupId,
                'name' => $groupName,
                'members' => $membersValue,
            ]);
        }
        header("Location: /admin/manage-groups.php");
        exit;
    }
}

$stmt = $pdo->query("SELECT id, name, members FROM groups ORDER BY name ASC");
$groups = $stmt->fetchAll(PDO::FETCH_ASSOC);
$editGroupId = $_GET['edit'] ?? '';
$editGroup = null;
foreach ($groups as $group) {
    if ((string)$group['id'] === (string)$editGroupId) {
        $editGroup = $group;
        break;
    }
}
$selectedMembers = [];
if ($editGroup) {
    $selectedMembers = preg_split('/[\s,]+/', $editGroup['members'] ?? '', -1, PREG_SPLIT_NO_EMPTY);
}
$showEditor = $editGroup || isset($_GET['new']);
$outputEndpointMap = [];
foreach ($outputEndpoints as $endpoint) {
    $outputEndpointMap[$endpoint['value']] = $endpoint['label'];
}

function group_member_count($members) {
    $parts = preg_split('/[\s,]+/', $members ?? '', -1, PREG_SPLIT_NO_EMPTY);
    return count($parts);
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Manage Groups - <?= htmlspecialchars($product_name) ?></title>
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
.header-actions { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:18px; }
.card { background:#FFF; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); padding:16px; }
.card h2 { margin:0 0 14px 0; font-size:1.1em; font-weight:500; color:#1976D2; }
.field { display:flex; flex-direction:column; gap:6px; margin-bottom:14px; }
.field label { color:#555; font-size:0.9em; }
.field input { border:1px solid #CCC; border-radius:4px; padding:10px; font:inherit; }
.btn-primary { background:#1976D2; color:#FFF; border:none; border-radius:4px; padding:10px 14px; cursor:pointer; font:inherit; text-decoration:none; display:inline-flex; align-items:center; gap:8px; }
.btn-secondary { color:#1976D2; text-decoration:none; padding:10px 12px; display:inline-flex; align-items:center; }
.group-list { list-style:none; margin:0; padding:0; }
.group-item { display:flex; justify-content:space-between; gap:14px; padding:14px 0; border-bottom:1px solid #EEE; }
.group-item:last-child { border-bottom:none; }
.group-main { flex:1; min-width:0; }
.group-name { font-weight:500; color:#202124; }
.group-members { color:#666; font-size:0.9em; margin-top:4px; overflow-wrap:anywhere; }
.group-actions { display:flex; align-items:center; gap:4px; }
.icon-action { width:36px; height:36px; border-radius:50%; color:#555; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; border:none; background:transparent; cursor:pointer; }
.icon-action:hover { background:rgba(25,118,210,0.08); color:#1976D2; }
.icon-action.delete:hover { background:rgba(198,40,40,0.08); color:#C62828; }
.editor-card { margin-top:18px; }
.transfer-list-container { display:flex; gap:14px; align-items:stretch; margin-top:8px; }
.tl-panel { flex:1; min-width:0; }
.tl-header { font-weight:500; margin-bottom:8px; color:#555; }
.tl-search { width:100%; box-sizing:border-box; border:1px solid #CCC; border-radius:4px; padding:9px; margin-bottom:8px; font:inherit; }
.tl-list { min-height:280px; max-height:360px; overflow:auto; border:1px solid #EEE; border-radius:8px; padding:8px; background:#FFF; }
.tl-item { padding:8px 10px; margin-bottom:4px; background:#FAFAFA; border:1px solid #EEE; cursor:pointer; user-select:none; border-radius:3px; font-size:0.95em; }
.tl-item.selected { background:#1976D2; color:#FFF; border-color:#1565C0; }
.tl-item.dragging { opacity:0.5; }
.tl-controls { display:flex; flex-direction:column; justify-content:center; gap:8px; }
.error { background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; padding:12px; border-radius:8px; margin-bottom:16px; }
.muted { color:#777; font-size:0.9em; }
@media(max-width:900px){ .transfer-list-container{ flex-direction:column; } .tl-controls{ flex-direction:row; justify-content:flex-start; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.card{ border:1px solid #333; background-color:#1E1E1E; }
.card h2 { color:#BB86FC; }
.field label,.muted,.group-members{ color:#BBB; }
.field input { background:#121212; border-color:#444; color:#E0E0E0; }
.btn-primary { background:#BB86FC; color:#000; }
.btn-secondary { color:#BB86FC; }
.group-item { border-bottom:1px solid #333; }
.group-name { color:#EDEDED; }
.tl-header { color:#BBB; }
.tl-search { background:#121212; border-color:#444; color:#E0E0E0; }
.tl-list { background:#121212; border-color:#333; }
.tl-item { background:#242424; border-color:#333; }
.tl-item.selected { background:#BB86FC; color:#000; border-color:#A370F7; }
.icon-action { color:#BBB; }
.icon-action:hover { background:rgba(187,134,252,0.1); color:#BB86FC; }
.icon-action.delete:hover { background:rgba(244,67,54,0.12); color:#EF9A9A; }
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
    <a href="/admin/manage-endpoints.php" class="admin-only"><i class="fa-solid fa-shapes"></i> Manage Endpoints</a>
    <a href="/admin/manage-groups.php" class="active admin-only"><i class="fa-solid fa-user-group"></i> Manage Groups</a>
    <a href="/admin/settings/general.php" class="admin-only"><i class="fa-solid fa-cogs"></i> Server Settings</a>
    <?php if ($show_online_docs == '1'): ?>
    <a href="https://docs.openpagingserver.org"><i class="fa-solid fa-book"></i> Online Documentation</a>
    <?php endif; ?>
    <button class="logout-btn-mobile" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
    <button class="logout-btn" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
</div>
<div id="content" onclick="closeSidebarOnContentClick()">
    <div class="header-actions">
        <h1><?= $showEditor ? ($editGroup ? 'Edit Group' : 'New Group') : 'Manage Groups' ?></h1>
        <?php if ($showEditor): ?>
            <a class="btn-secondary" href="/admin/manage-groups.php"><i class="fa-solid fa-arrow-left"></i> Back</a>
        <?php else: ?>
            <a class="btn-primary" href="/admin/manage-groups.php?new=1"><i class="fa-solid fa-plus"></i> New Group</a>
        <?php endif; ?>
    </div>
    <?php if ($endpointError): ?><div class="error"><?= htmlspecialchars($endpointError) ?></div><?php endif; ?>
    <?php if ($endpointWarning): ?><div class="error"><?= htmlspecialchars($endpointWarning) ?></div><?php endif; ?>
    <?php foreach ($endpointModuleErrors as $moduleError): ?><div class="error"><?= htmlspecialchars($moduleError) ?></div><?php endforeach; ?>
    <?php if (!$showEditor): ?>
        <div class="card">
            <h2>Groups</h2>
            <?php if (empty($groups)): ?>
                <p class="muted">No groups yet.</p>
            <?php else: ?>
                <ul class="group-list">
                    <?php foreach ($groups as $group): ?>
                        <li class="group-item">
                            <div class="group-main">
                                <div class="group-name"><?= htmlspecialchars($group['name'] ?? '') ?></div>
                                <div class="group-members"><?= htmlspecialchars((string)group_member_count($group['members'] ?? '')) ?> member<?= group_member_count($group['members'] ?? '') === 1 ? '' : 's' ?></div>
                            </div>
                            <div class="group-actions">
                                <a class="icon-action" href="/admin/manage-groups.php?edit=<?= urlencode($group['id']) ?>" title="Edit"><i class="fa-solid fa-pen-to-square"></i></a>
                                <form method="POST" action="/admin/manage-groups.php" onsubmit="return confirm('Delete this group?')">
                                    <input type="hidden" name="action" value="delete">
                                    <input type="hidden" name="group_id" value="<?= htmlspecialchars($group['id']) ?>">
                                    <button class="icon-action delete" type="submit" title="Delete"><i class="fa-solid fa-trash"></i></button>
                                </form>
                            </div>
                        </li>
                    <?php endforeach; ?>
                </ul>
            <?php endif; ?>
        </div>
    <?php else: ?>
        <form class="card editor-card" method="POST" action="/admin/manage-groups.php" onsubmit="syncSelectedInputs()">
            <h2><?= $editGroup ? 'Edit Group' : 'New Group' ?></h2>
            <input type="hidden" name="action" value="save">
            <input type="hidden" name="group_id" value="<?= htmlspecialchars($editGroup['id'] ?? '') ?>">
            <div class="field">
                <label for="name">Name</label>
                <input id="name" name="name" value="<?= htmlspecialchars($editGroup['name'] ?? '') ?>" required>
            </div>
            <?php if (empty($outputEndpoints)): ?>
                <p class="muted">No output endpoints available.</p>
            <?php else: ?>
                <div class="transfer-list-container">
                    <div class="tl-panel">
                        <div class="tl-header">Available Devices</div>
                        <input type="text" id="deviceSearch" class="tl-search" placeholder="Search devices..." onkeyup="filterDevices()">
                        <div class="tl-list" id="availableDeviceList" ondrop="dropToAvailable(event)" ondragover="allowDrop(event)">
                            <?php foreach ($outputEndpoints as $endpoint): ?>
                                <?php if (in_array($endpoint['value'], $selectedMembers, true)) continue; ?>
                                <div class="tl-item" draggable="true" ondragstart="dragStart(event)" onclick="selectItem(this)" data-value="<?= htmlspecialchars($endpoint['value']) ?>">
                                    <?= htmlspecialchars($endpoint['label']) ?>
                                </div>
                            <?php endforeach; ?>
                        </div>
                    </div>
                    <div class="tl-controls">
                        <button type="button" class="btn-primary" onclick="moveRight()" title="Move Selected Right"><i class="fa-solid fa-angle-right"></i></button>
                        <button type="button" class="btn-primary" onclick="moveLeft()" title="Move Selected Left"><i class="fa-solid fa-angle-left"></i></button>
                        <button type="button" class="btn-primary" onclick="moveUp()" title="Move Selected Up"><i class="fa-solid fa-angle-up"></i></button>
                        <button type="button" class="btn-primary" onclick="moveDown()" title="Move Selected Down"><i class="fa-solid fa-angle-down"></i></button>
                    </div>
                    <div class="tl-panel">
                        <div class="tl-header">Selected Devices</div>
                        <div class="tl-list" id="selectedDeviceList" ondrop="dropToSelected(event)" ondragover="allowDrop(event)">
                            <?php foreach ($selectedMembers as $member): ?>
                                <?php if (!isset($outputEndpointMap[$member])) continue; ?>
                                <div class="tl-item" draggable="true" ondragstart="dragStart(event)" onclick="selectItem(this)" data-value="<?= htmlspecialchars($member) ?>">
                                    <?= htmlspecialchars($outputEndpointMap[$member]) ?>
                                    <input type="hidden" name="members[]" value="<?= htmlspecialchars($member) ?>">
                                </div>
                            <?php endforeach; ?>
                        </div>
                    </div>
                </div>
            <?php endif; ?>
            <div style="margin-top:16px;">
                <button class="btn-primary" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save Group</button>
                <a class="btn-secondary" href="/admin/manage-groups.php">Cancel</a>
            </div>
        </form>
    <?php endif; ?>
</div>
<script>
let draggedItem = null;

function selectItem(el) {
  const siblings = el.parentElement.querySelectorAll('.tl-item');
  siblings.forEach(s => s.classList.remove('selected'));
  el.classList.add('selected');
}

function filterDevices() {
  const search = document.getElementById('deviceSearch').value.toLowerCase();
  const items = document.getElementById('availableDeviceList').querySelectorAll('.tl-item');
  items.forEach(item => {
    if (item.innerText.toLowerCase().includes(search)) {
      item.style.display = '';
    } else {
      item.style.display = 'none';
      item.classList.remove('selected');
    }
  });
}

function appendHiddenInput(item) {
  if (!item.querySelector('input[type="hidden"]')) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'members[]';
    input.value = item.getAttribute('data-value');
    item.appendChild(input);
  }
}

function removeHiddenInput(item) {
  const input = item.querySelector('input[type="hidden"]');
  if (input) input.remove();
}

function moveRight() {
  const selected = document.querySelector('#availableDeviceList .tl-item.selected');
  if (selected) {
    selected.classList.remove('selected');
    appendHiddenInput(selected);
    document.getElementById('selectedDeviceList').appendChild(selected);
  }
}

function moveLeft() {
  const selected = document.querySelector('#selectedDeviceList .tl-item.selected');
  if (selected) {
    selected.classList.remove('selected');
    removeHiddenInput(selected);
    document.getElementById('availableDeviceList').appendChild(selected);
    filterDevices();
  }
}

function moveUp() {
  const selected = document.querySelector('#selectedDeviceList .tl-item.selected');
  if (selected && selected.previousElementSibling) {
    selected.parentNode.insertBefore(selected, selected.previousElementSibling);
  }
}

function moveDown() {
  const selected = document.querySelector('#selectedDeviceList .tl-item.selected');
  if (selected && selected.nextElementSibling) {
    selected.parentNode.insertBefore(selected.nextElementSibling, selected);
  }
}

function dragStart(e) {
  draggedItem = e.target;
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', draggedItem.getAttribute('data-value'));
  setTimeout(() => draggedItem.classList.add('dragging'), 0);
}

document.addEventListener('dragend', function(e) {
  if (e.target.classList && e.target.classList.contains('tl-item')) {
    e.target.classList.remove('dragging');
    draggedItem = null;
  }
});

function allowDrop(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
}

function dropToAvailable(e) {
  e.preventDefault();
  if (draggedItem && draggedItem.parentElement.id === 'selectedDeviceList') {
    removeHiddenInput(draggedItem);
    draggedItem.classList.remove('selected');
    document.getElementById('availableDeviceList').appendChild(draggedItem);
    filterDevices();
  }
}

function dropToSelected(e) {
  e.preventDefault();
  if (!draggedItem) return;
  if (draggedItem.parentElement.id === 'availableDeviceList') {
    appendHiddenInput(draggedItem);
    draggedItem.classList.remove('selected');
  }
  const list = document.getElementById('selectedDeviceList');
  const afterElement = getDragAfterElement(list, e.clientY);
  if (afterElement == null) {
    list.appendChild(draggedItem);
  } else {
    list.insertBefore(draggedItem, afterElement);
  }
}

function getDragAfterElement(container, y) {
  const draggableElements = [...container.querySelectorAll('.tl-item:not(.dragging)')];
  return draggableElements.reduce((closest, child) => {
    const box = child.getBoundingClientRect();
    const offset = y - box.top - box.height / 2;
    if (offset < 0 && offset > closest.offset) {
      return { offset: offset, element: child };
    }
    return closest;
  }, { offset: Number.NEGATIVE_INFINITY }).element;
}

function syncSelectedInputs() {
  document.querySelectorAll('#selectedDeviceList .tl-item').forEach(appendHiddenInput);
}

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
