<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);
session_start();
require_once __DIR__ . '/../config.php';
require_once __DIR__ . '/../includes/sidebar-brand.php';
require_once __DIR__ . '/broadcast_helpers.php';

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
$sender = $user['username'] ?? ($_SESSION['username'] ?? 'User');
$isAdmin = ($userRole === 'admin' || $userRole === 'tempadmin');
$sendError = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['msgid'])) {
    $messageId = (string)$_POST['msgid'];
    
    if (isset($_POST['send_all'])) {
        $targets = '0';
    } else {
        if (!empty($_POST['groups']) && is_array($_POST['groups'])) {
            $clean_groups = array_map('intval', $_POST['groups']);
            $targets = implode('.', $clean_groups);
        } else {
            header("Location: /messages/send.php?msgid=" . urlencode($_POST['msgid']));
            exit;
        }
    }
    
    try {
        message_create_broadcast_from_template($pdo, $messageId, $targets, $sender);
    } catch (Throwable $e) {
        $sendError = "Failed to send message: " . $e->getMessage();
        error_log("Failed to create broadcast from message template {$messageId}: " . $e->getMessage());
    }
    if ($sendError === '') {
        header("Location: /messages");
        exit;
    }
}

if (!isset($_GET['msgid'])) {
    header("Location: /messages");
    exit;
}

$msgid = $_GET['msgid'];

$stmt = $pdo->prepare("SELECT name FROM messages WHERE messageid = :id LIMIT 1");
$stmt->execute(['id' => $msgid]);
$messageName = $stmt->fetchColumn();

if (!$messageName) {
    header("Location: /messages");
    exit;
}

$stmt = $pdo->query("SELECT id, name FROM groups ORDER BY name ASC");
$groups = $stmt->fetchAll(PDO::FETCH_ASSOC);

$stmt = $pdo->query("SELECT parameter, value FROM systemsettings");
$settings = [];
foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
    $settings[$row['parameter']] = $row['value'];
}
$product_name = $settings['product_name'] ?? 'Open Paging Server';
$favicon = $settings['favicon'] ?? '';
$show_online_docs = $settings['show_online_docs'] ?? '1';

?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Sending <?= htmlspecialchars($messageName) ?> - <?= htmlspecialchars($product_name) ?></title>
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
#content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
#content h1{ font-weight:400; }
.info-card{ background:#FFF; padding:16px; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; }
.info-row { display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid #f0f0f0; align-items: center; }
.info-row:last-child { border-bottom:none; }
.info-label { font-weight:500; color:#555; }
.md-checkbox-container { display:flex; align-items:center; position:relative; cursor:pointer; font-size:14px; font-weight:500; color:#555; user-select:none; width:100%; padding: 5px 0; }
.md-checkbox-container input { position:absolute; opacity:0; cursor:pointer; height:0; width:0; }
.md-checkmark { position:relative; display:inline-block; height:20px; width:20px; background-color:#fff; border:2px solid #5f6368; border-radius:2px; margin-right:12px; transition:all 0.2s; }
.md-checkbox-container:hover input ~ .md-checkmark { border-color:#202124; }
.md-checkbox-container input:checked ~ .md-checkmark { background-color:#1976D2; border-color:#1976D2; }
.md-checkmark:after { content:""; position:absolute; display:none; left:6px; top:2px; width:4px; height:10px; border:solid white; border-width:0 2px 2px 0; transform:rotate(45deg); }
.md-checkbox-container input:checked ~ .md-checkmark:after { display:block; }
.md-checkbox-container input:disabled ~ .md-checkmark { border-color:#dadce0; background-color:#f1f3f4; cursor:not-allowed; }
.md-checkbox-container input:disabled ~ .text { color:#9aa0a6; cursor:not-allowed; }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.info-card{ border:1px solid #333; background-color:#1E1E1E; }
.info-row { border-bottom:1px solid #333; }
.md-checkbox-container { color:#BBB; }
.md-checkmark { border-color:#9AA0A6; background-color:#1E1E1E; }
.md-checkbox-container:hover input ~ .md-checkmark { border-color:#E8EAED; }
.md-checkbox-container input:checked ~ .md-checkmark { background-color:#8AB4F8; border-color:#8AB4F8; }
.md-checkmark:after { border-color:#1E1E1E; }
.md-checkbox-container input:disabled ~ .md-checkmark { border-color:#5F6368; background-color:#3C4043; }
.md-checkbox-container input:disabled ~ .text { color:#5F6368; }
}
.header-actions { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.btn-primary { background:#1976D2; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; }
.btn-primary:hover { background:#1565C0; }
.btn-send { background:#2E7D32; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; transition: all 0.2s ease; box-shadow: 0 1px 2px rgba(0,0,0,0.1); }
.btn-send:hover { background:#1B5E20; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
.btn-cancel { background:#757575; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; transition: all 0.2s ease; box-shadow: 0 1px 2px rgba(0,0,0,0.1); }
.btn-cancel:hover { background:#616161; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
@media(prefers-color-scheme:dark){ 
    .btn-primary { background:#BB86FC; color:#000; } 
    .btn-primary:hover { background:#A370F7; } 
    .btn-send { background:#81C784; color:#000; }
    .btn-send:hover { background:#66BB6A; }
    .btn-cancel { background:#B0BEC5; color:#000; }
    .btn-cancel:hover { background:#90A4AE; }
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
    <a href="/messages" class="active"><i class="fa-solid fa-message"></i> Messages</a>
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
    <div class="header-actions">
        <h1>Sending <?= htmlspecialchars($messageName) ?></h1>
    </div>
    <?php if ($sendError !== ''): ?>
        <div class="info-card" style="border-color:#C62828;color:#C62828;">
            <?= htmlspecialchars($sendError) ?>
        </div>
    <?php endif; ?>

    <form action="/messages/send.php?msgid=<?= urlencode($msgid) ?>" method="POST" id="sendForm">
        <input type="hidden" name="msgid" value="<?= htmlspecialchars($msgid) ?>">
        <div class="info-card">
            <div class="info-row">
                <label class="md-checkbox-container">
                    <input type="checkbox" name="send_all" id="send_all" value="1">
                    <span class="md-checkmark"></span>
                    <span class="text" style="font-weight: bold; color: #1976D2;">All Recipients</span>
                </label>
            </div>
            
            <?php if (empty($groups)): ?>
                <div class="info-row">
                    <span class="info-label" style="color:#777;">No groups available.</span>
                </div>
            <?php else: ?>
                <?php foreach ($groups as $group): ?>
                    <div class="info-row">
                        <label class="md-checkbox-container">
                            <input type="checkbox" name="groups[]" value="<?= htmlspecialchars($group['id']) ?>" class="group-checkbox">
                            <span class="md-checkmark"></span>
                            <span class="text"><?= htmlspecialchars($group['name']) ?></span>
                        </label>
                    </div>
                <?php endforeach; ?>
            <?php endif; ?>

            <div class="info-row" style="margin-top: 20px; justify-content: flex-end; gap: 15px; border-bottom: none;">
                <a href="/messages" class="btn-cancel">Cancel</a>
                <button type="submit" class="btn-send"><i class="fa-solid fa-paper-plane" style="margin-right:8px;"></i> Send Message</button>
            </div>
        </div>
    </form>
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

document.getElementById('send_all').addEventListener('change', function() {
    var isChecked = this.checked;
    var checkboxes = document.querySelectorAll('.group-checkbox');
    checkboxes.forEach(function(checkbox) {
        checkbox.disabled = isChecked;
        if (isChecked) {
            checkbox.checked = false;
        }
    });
});
</script>
</body>
</html>
