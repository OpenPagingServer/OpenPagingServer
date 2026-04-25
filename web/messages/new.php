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

if ($userRole !== 'admin' && $userRole !== 'tempadmin') {
    http_response_code(403);
    echo "403 Forbidden - Admin access required.";
    exit;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $name = $_POST['name'] ?? '';
    $type = $_POST['type'] ?? '';
    $shortmessage = $_POST['shortmessage'] ?? '';
    $longmessage = $_POST['longmessage'] ?? '';
    $audio = $_POST['audio'] ?? '';

    $stmt = $pdo->query("SELECT messageid FROM messages ORDER BY messageid ASC");
    $existingIds = $stmt->fetchAll(PDO::FETCH_COLUMN);
    
    $newId = 1;
    foreach ($existingIds as $id) {
        if ($id == $newId) {
            $newId++;
        } else if ($id > $newId) {
            break;
        }
    }

    $stmt = $pdo->prepare("INSERT INTO messages (messageid, name, type, shortmessage, longmessage, audio) VALUES (:id, :name, :type, :shortmessage, :longmessage, :audio)");
    $stmt->execute([
        'id' => $newId,
        'name' => $name,
        'type' => $type,
        'shortmessage' => $shortmessage,
        'longmessage' => $longmessage,
        'audio' => $audio
    ]);

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

$stmt = $pdo->query("SELECT path, webpath, webroles, webinterface, webname, webicon FROM enabledmodules WHERE status = 1 ORDER BY path ASC");
$modules = $stmt->fetchAll(PDO::FETCH_ASSOC);
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>New Message - <?= htmlspecialchars($product_name) ?></title>
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
.form-group { margin-bottom: 20px; }
.form-group label { display: block; margin-bottom: 8px; font-weight: 500; }
.form-control { width: 100%; padding: 10px; border: 1px solid #DDD; border-radius: 4px; box-sizing: border-box; background: #FFF; color: #000; }
@media(prefers-color-scheme:dark){
    .form-control { background: #333; border: 1px solid #444; color: #FFF; }
    .btn-primary { background:#BB86FC; color:#000; } 
    .btn-primary:hover { background:#A370F7; }
}
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
    <a href="/messages" class="active"><i class="fa-solid fa-message"></i> Messages</a>
    <a href="/history.php"><i class="fa-solid fa-clock-rotate-left"></i> History</a>
    <?php foreach ($modules as $mod):
        if ($mod['webinterface'] != 1) continue;
        $allowedRoles = array_map('trim', explode(',', $mod['webroles']));
        if (!in_array($userRole, $allowedRoles)) continue;
    ?>
        <a href="<?= htmlspecialchars($mod['webpath']) ?>">
            <i class="fa-solid <?= htmlspecialchars($mod['webicon']) ?: 'fa-circle' ?>"></i> <?= htmlspecialchars($mod['webname']) ?>
        </a>
    <?php endforeach; ?>
    <a href="/admin/manage-users.php" class="admin-only"><i class="fa-solid fa-users-cog"></i> Manage Users</a>
    <a href="/admin/manage-endpoints.php" class="admin-only"><i class="fa-solid fa-shapes"></i> Manage Endpoints</a>
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
        <h1>New Message</h1>
    </div>

    <div class="info-card">
        <form method="POST">
            <div class="form-group">
                <label for="type">Message Type</label>
                <select name="type" id="type" class="form-control" required onchange="toggleFields()">
                    <option value="" disabled selected>Select a type</option>
                    <option value="text+audio">Pre-recorded audio with text (text+audio)</option>
                    <option value="audio">Pre-recorded audio (audio)</option>
                    <option value="text">Text (text)</option>
                    <option value="text+audio+live">Pre-recorded audio with text then live paging (text+audio+live)</option>
                </select>
            </div>

            <div class="form-group">
                <label for="name">Name</label>
                <input type="text" name="name" id="name" class="form-control" required>
            </div>

            <div id="text-fields" style="display:none;">
                <div class="form-group">
                    <label for="shortmessage">Short Message</label>
                    <input type="text" name="shortmessage" id="shortmessage" class="form-control">
                </div>
                <div class="form-group">
                    <label for="longmessage">Long Message</label>
                    <textarea name="longmessage" id="longmessage" class="form-control" rows="4"></textarea>
                </div>
            </div>

            <div id="audio-fields" style="display:none;">
                <div class="form-group">
                    <label for="audio">Audio Parameter (Filename)</label>
                    <input type="text" name="audio" id="audio" class="form-control">
                </div>
            </div>

            <div style="margin-top: 20px;">
                <button type="submit" class="btn-primary">Create Message</button>
                <a href="/messages" style="margin-left:10px; color:#777; text-decoration:none;">Cancel</a>
            </div>
        </form>
    </div>
</div>
<script>
function toggleFields() {
    const type = document.getElementById('type').value;
    const textFields = document.getElementById('text-fields');
    const audioFields = document.getElementById('audio-fields');

    textFields.style.display = 'none';
    audioFields.style.display = 'none';

    if (type === 'text' || type === 'text+audio' || type === 'text+audio+live') {
        textFields.style.display = 'block';
    }
    
    if (type === 'audio' || type === 'text+audio' || type === 'text+audio+live') {
        audioFields.style.display = 'block';
    }
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
