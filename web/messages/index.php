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

if (isset($_GET['delete_msgid']) && $isAdmin) {
    $stmt = $pdo->prepare("DELETE FROM messages WHERE messageid = :id");
    $stmt->execute(['id' => $_GET['delete_msgid']]);
    header("Location: /messages");
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

$stmt = $pdo->query("SELECT messageid, name, type FROM messages ORDER BY name ASC");
$messages = $stmt->fetchAll(PDO::FETCH_ASSOC);

?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Messages - <?= htmlspecialchars($product_name) ?></title>
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
.info-card{ background:#FFF; padding:16px; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; }
.info-row { display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid #f0f0f0; align-items: center; }
.info-row:last-child { border-bottom:none; }
.info-label { font-weight:500; color:#555; }
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
}
.header-actions { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.btn-primary { background:#1976D2; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; }
.btn-primary:hover { background:#1565C0; }
.btn-custom-send { background:#2E7D32; color:#FFF; border:none; padding:10px 16px; border-radius:6px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; }
.btn-custom-send:hover { background:#1B5E20; }
.btn-send { background:#2E7D32; color:#FFF; border:none; padding:8px 12px; border-radius:4px; font-size:13px; cursor:pointer; text-decoration:none; }
.btn-send:hover { background:#1B5E20; }
@media(prefers-color-scheme:dark){ .btn-primary { background:#BB86FC; color:#000; } .btn-primary:hover { background:#A370F7; } .btn-custom-send { background:#81C784; color:#000; } .btn-custom-send:hover { background:#66BB6A; } }
.msg-type { font-size: 0.8em; color: #777; font-weight: 400; display: block; }
.dropdown { position: relative; display: inline-block; }
.dropbtn { background: none; border: none; font-size: 1.2em; cursor: pointer; color: #777; padding: 5px 10px; }
.dropdown-content { display: none; position: absolute; right: 0; background-color: #f9f9f9; min-width: 120px; box-shadow: 0px 8px 166px 0px rgba(0,0,0,0.2); z-index: 1; border-radius: 4px; }
.dropdown-content a { color: black; padding: 12px 16px; text-decoration: none; display: block; font-size: 14px; }
.dropdown-content a:hover { background-color: #f1f1f1; }
@media(prefers-color-scheme:dark){
    .msg-type { color: #AAA; }
    .dropbtn { color: #BBB; }
    .dropdown-content { background-color: #333; }
    .dropdown-content a { color: #EEE; }
    .dropdown-content a:hover { background-color: #444; }
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
        <h1>Messages</h1>
        <div style="display:flex; gap:10px; align-items:center;">
        <a href="/messages/custom.php" class="btn-custom-send"><i class="fa-solid fa-paper-plane" style="margin-right:8px;"></i> Send Custom Message</a>
        <?php if ($isAdmin): ?>
        <a href="/messages/new.php" class="btn-primary"><i class="fa-solid fa-plus" style="margin-right:8px;"></i> New Message</a>
        <?php endif; ?>
        </div>
    </div>

    <div class="info-card">
        <?php if (empty($messages)): ?>
            <p style="text-align:center; color:#777; padding: 20px;">No messages</p>
        <?php else: ?>
            <?php foreach ($messages as $msg): ?>
                <div class="info-row">
                    <div>
                        <span class="info-label"><?= htmlspecialchars($msg['name']) ?></span>
                        <span class="msg-type"><?= htmlspecialchars($msg['type']) ?></span>
                    </div>
                    <div style="display:flex; align-items:center; gap:10px;">
                        <a href="/messages/send.php?msgid=<?= urlencode($msg['messageid']) ?>" class="btn-send"><i class="fa-solid fa-paper-plane"></i> Send</a>
                        
                        <?php if ($isAdmin): ?>
                        <div class="dropdown">
                            <button class="dropbtn" onclick="event.stopPropagation(); toggleDropdown(this);"><i class="fa-solid fa-ellipsis-vertical"></i></button>
                            <div class="dropdown-content">
                                <a href="/messages/edit.php?msgid=<?= urlencode($msg['messageid']) ?>"><i class="fa-solid fa-pen-to-square"></i> Edit</a>
                                <a href="?delete_msgid=<?= urlencode($msg['messageid']) ?>" onclick="return confirm('Are you sure you want to delete this message?')" style="color:#C62828;"><i class="fa-solid fa-trash"></i> Delete</a>
                            </div>
                        </div>
                        <?php endif; ?>
                    </div>
                </div>
            <?php endforeach; ?>
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

function toggleDropdown(btn) {
    document.querySelectorAll('.dropdown-content.open').forEach(function(el) {
        if (!el.contains(btn)) el.classList.remove('open');
    });
    var menu = btn.nextElementSibling;
    if (menu) menu.classList.toggle('open');
}
document.addEventListener('click', function(event) {
    document.querySelectorAll('.dropdown-content.open').forEach(function(el) {
        if (!el.contains(event.target) && !el.previousElementSibling.contains(event.target)) {
            el.classList.remove('open');
        }
    });
});
</script>
<style>
.dropdown-content.open { display: block; }
</style>
</body>
</html>
