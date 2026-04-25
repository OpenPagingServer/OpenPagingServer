<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);

session_start();
require_once 'config.php';

$is_insecure = (!isset($_SERVER['HTTPS']) || $_SERVER['HTTPS'] !== 'on');
if (isset($_SERVER['HTTP_X_FORWARDED_PROTO']) && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https') {
    $is_insecure = false;
}

if (!isset($_SESSION['user_id'])) {
    header("Location: /");
    exit;
}

$stmt = $pdo->prepare("SELECT role FROM users WHERE id = :id LIMIT 1");
$stmt->execute(['id' => $_SESSION['user_id']]);
$userRole = $stmt->fetchColumn();
$isAdmin = ($userRole === 'admin' || $userRole === 'tempadmin');

$username = $_SESSION['username'] ?? 'User';

$stmt = $pdo->query("SELECT path, webpath, webroles, webinterface, webname, webicon FROM enabledmodules WHERE status = 1 ORDER BY path ASC");
$modules = $stmt->fetchAll(PDO::FETCH_ASSOC);

$product_name = 'Open Paging Server';
$favicon = '';
$show_online_docs = '1';

$stmt_settings = $pdo->prepare("SELECT parameter, value FROM systemsettings WHERE parameter IN ('product_name','favicon','show_online_docs')");
$stmt_settings->execute();
$settings = $stmt_settings->fetchAll(PDO::FETCH_KEY_PAIR);

$product_name = $settings['product_name'] ?? $product_name;
$favicon = $settings['favicon'] ?? '';
$show_online_docs = $settings['show_online_docs'] ?? '1';
?>

<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Dashboard - <?= htmlspecialchars($product_name) ?></title>
<?php if (!empty($favicon)): ?>
<link rel="icon" href="<?= htmlspecialchars($favicon) ?>" type="image/x-icon">
<?php endif; ?>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet" />
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
    .logout-btn:active{ background-color:#A51B1B; }
    .logout-btn-mobile{ background-color:#C62828; border:none; cursor:pointer; transition:background-color 0.3s; display:none; }
    .logout-btn-mobile:hover{ background-color:#B71C1C; }
    @media(max-width:767px){ .logout-btn{ display:none; } .logout-btn-mobile{ display:block; } }
    #mobile-header{ display:flex; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
    #mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
    #mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
    #overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
    #overlay.active{ display:block; }
    #content{ margin-left:220px; padding:24px; height:100vh; overflow-y:auto; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
    @media(max-width:767px){ #content{ margin-left:0; width:100%; padding-top:70px; } }
    #content h2{ color:#1976D2; margin-bottom:16px; font-weight:400; display:flex; align-items:center; justify-content:space-between; }
    #content h1{ font-weight:400; }
    ul.voicemail-list{ list-style-type:none; padding:0; margin:0; display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:16px; }
    @media(max-width:767px){ ul.voicemail-list{ grid-template-columns:1fr; } }
    li.voicemail-card{ background:#FFF; padding:16px; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); display:flex; flex-direction:column; gap:8px; }
    li.voicemail-card audio{ width:100%; }
    .card-actions{ display:flex; gap:10px; flex-wrap:wrap; }
    .voicemail-info-grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:8px; }
    .flat-btn{ background:none; border:none; color:#6200ee; padding:8px 16px; font:inherit; cursor:pointer; transition:background-color 0.3s; font-size:0.9em; outline:none; border-radius:4px; }
    .flat-btn:hover{ background-color:rgba(98,0,238,0.08); }
    .flat-btn.delete{ color:#c62828; }
    .flat-btn.delete:hover{ background-color:rgba(198,40,40,0.08); }
    @media(min-width:768px){ #mobile-header{ display:none; } }
    @media(prefers-color-scheme:dark){ body,html{ background-color:#121212; color:#E0E0E0; } #sidebar{ background-color:#424242; } #sidebar h2{ background-color:#303030; color:#FFF; } #sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; } #sidebar a.active,#sidebar a:hover{ background-color:#505050; } #mobile-header{ background-color:#424242; } #mobile-header h2{ color:#FFF; } #content{ background-color:#121212; } li.voicemail-card{ border:1px solid #333; background-color:#1E1E1E; } h2,h3{ color:#BB86FC; } .flat-btn:hover{ background-color:rgba(187,134,252,0.1); } }
    .protocol-warning { background-color: rgba(255, 235, 59, 0.15); border: 1px solid #fbc02d; color: #856404; padding: 12px 20px; margin-bottom: 20px; border-radius: 8px; display: flex; align-items: center; gap: 12px; font-size: 0.95em; }
    .protocol-warning i { font-size: 1.2em; }
    @media (prefers-color-scheme: dark) { .protocol-warning { background-color: rgba(255, 235, 59, 0.05); color: #fff176; border-color: #fbc02d; } }
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
    <a href="/dashboard.php" class="active"><i class="fa-solid fa-house"></i> Dashboard</a>
    <a href="/paging"><i class="fa-solid fa-bullhorn"></i> Paging</a>
    <a href="/messages"><i class="fa-solid fa-message"></i> Messages</a>
    <a href="/history"><i class="fa-solid fa-clock-rotate-left"></i> History</a>

    <?php foreach ($modules as $mod): 
        if ($mod['webinterface'] != 1) continue;
        $allowedRoles = array_map('trim', explode(',', $mod['webroles']));
        if (!in_array($userRole, $allowedRoles)) continue;
        $link = htmlspecialchars($mod['webpath']);
        $name = htmlspecialchars($mod['webname']);
        $icon = htmlspecialchars($mod['webicon']) ?: 'fa-circle';
    ?>
        <a href="<?php echo $link; ?>">
            <i class="fa-solid <?php echo $icon; ?>"></i> <?php echo $name; ?>
        </a>
    <?php endforeach; ?>

    <?php if ($isAdmin): ?>
      <a href="/admin/manage-users.php" class="admin-only"><i class="fa-solid fa-users-cog"></i> Manage Users</a>
      <a href="/admin/manage-endpoints.php" class="admin-only"><i class="fa-solid fa-shapes"></i> Manage Endpoints</a>
      <a href="/admin/manage-groups.php"><i class="fa-solid fa-user-group"></i> Manage Groups</a>
      <a href="/admin/settings/general.php" class="admin-only"><i class="fa-solid fa-cogs"></i> Server Settings</a>
    <?php endif; ?>

    <?php if ($show_online_docs == '1'): ?>
    <a href="https://docs.openpagingserver.org"><i class="fa-solid fa-book"></i> Online Documentation</a>
    <?php endif; ?>

    <button class="logout-btn-mobile" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
    <button class="logout-btn" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
</div>

<div id="content" onclick="closeSidebarOnContentClick()">
    <?php if ($is_insecure): ?>
      <div class="protocol-warning">
        <i class="fa-solid fa-triangle-exclamation"></i>
        <span>You are connected to the server over plain HTTP. Content sent is not encrypted while in transit. Avoid sending private or confidential information if possible until this is resolved.</span>
      </div>
    <?php endif; ?>
    <h1>Hey there, <span id="extension-name"><?php echo htmlspecialchars($username); ?></span></h1>
    <p>Thank you for trying the Open Paging Server Beta. In the future, this page will be used to show you currently active messages, and the ability to quickly trigger certain actions.</p>
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
