<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);

session_start();
require_once '/var/www/html/config.php';

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
if (!$isAdmin) {
    http_response_code(403);
    header('Content-Type: text/html; charset=UTF-8');
    readfile('/var/www/html/.errors/403.html');
    exit;
}
$username = $_SESSION['username'] ?? 'User';

$stmt = $pdo->query("SELECT path, webpath, webroles, webinterface, webname, webicon FROM enabledmodules WHERE status = 1 ORDER BY path ASC");
$modules = $stmt->fetchAll(PDO::FETCH_ASSOC);

$stmt = $pdo->query("SELECT parameter, value FROM systemsettings");
$settings = [];
foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
    $settings[$row['parameter']] = $row['value'];
}

$product_name = $settings['product_name'] ?? 'Open Paging Server';
$favicon = $settings['favicon'] ?? '';
$show_online_docs = $settings['show_online_docs'] ?? '1';

function is_private_ip($ip) {
    return filter_var(
        $ip,
        FILTER_VALIDATE_IP,
        FILTER_FLAG_IPV4 | FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE
    ) === false;
}

function get_network_info() {
    $info = [];
    $ipv4 = shell_exec("ip -4 addr show | grep inet | awk '{print $2}' | cut -d/ -f1");
    $ipv6 = shell_exec("ip -6 addr show | grep inet6 | awk '{print $2}' | cut -d/ -f1");
    $ipv4_list = array_filter(array_map('trim', explode("\n", (string)$ipv4)));
    $ipv6_list = array_filter(array_map('trim', explode("\n", (string)$ipv6)));
    $ipv4_list = array_values(array_diff($ipv4_list, ['127.0.0.1']));
    $ipv6_list = array_values(array_diff($ipv6_list, ['::1']));
    $private = [];
    $public = [];
    foreach ($ipv4_list as $ip) {
        if (is_private_ip($ip)) {
            $private[] = $ip;
        } else {
            $public[] = $ip;
        }
    }
    $dns_raw = @file_get_contents('/etc/resolv.conf');
    preg_match_all('/^nameserver\s+([^\s]+)/m', (string)$dns_raw, $dns_matches);
    $dns = $dns_matches[1] ?? [];
    $gateway = trim((string)shell_exec("ip route | grep default | awk '{print $3}'"));
    $public_ip_api = @file_get_contents('https://analytics.openpagingserver.org/ipaddr/');
    $public_ip_api = $public_ip_api ? trim($public_ip_api) : null;
    $info['private_ipv4'] = $private;
    $info['public_ipv4'] = $public;
    $info['public_detected'] = $public_ip_api;
    $info['dns'] = $dns;
    $info['gateway'] = $gateway ?: 'Unknown';
    $info['ipv6'] = $ipv6_list;
    return $info;
}

function get_system_info() {
    $info = [];
    $info['os'] = php_uname('s') . ' ' . php_uname('r');
    $info['hostname'] = php_uname('n');
    if (strtoupper(substr(PHP_OS, 0, 3)) === 'WIN') {
        $info['cpu'] = "Information unavailable on Windows";
        $info['uptime'] = "Information unavailable on Windows";
    } else {
        $cpu = shell_exec("grep 'model name' /proc/cpuinfo | head -1 | cut -d: -f2");
        $info['cpu'] = $cpu ? trim($cpu) : "Unknown CPU";
        $uptime = shell_exec("uptime -p");
        $info['uptime'] = $uptime ? trim($uptime) : "Unknown";
        $mem = shell_exec("free -m | grep Mem | awk '{print $2}'");
        $info['ram'] = $mem ? trim($mem) . " MB" : "Unknown";
    }
    $info['php_version'] = PHP_VERSION;
    return $info;
}

$sysInfo = get_system_info();
$netInfo = get_network_info();
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>About - <?= htmlspecialchars($product_name) ?></title>
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
#content h2{ color:#1976D2; margin-bottom:16px; font-weight:400; }
#content h1{ font-weight:400; }
.info-card{ background:#FFF; padding:16px; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); margin-bottom:16px; }
.info-row { display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid #f0f0f0; align-items: center; }
.info-row:last-child { border-bottom:none; }
.info-label { font-weight:500; color:#555; }
.server-image { width:300px; height:auto; margin:0 auto 24px auto; display:block; border-radius:12px; }

.tabs-container { margin-bottom: 20px; border-bottom: 1px solid #DDD; }
.tabs-desktop { display: flex; gap: 10px; }
.tab-link { padding: 10px 20px; cursor: pointer; border: 1px solid transparent; border-bottom: none; border-radius: 5px 5px 0 0; background: #f5f5f5; color: #555; transition: 0.3s; text-decoration: none; }
.tab-link.active { background: #1976D2; color: #FFF; border-color: #1976D2; }
.tabs-mobile { display: none; width: 100%; padding: 10px; border-radius: 5px; border: 1px solid #CCC; margin-bottom: 15px; font-size: 16px; }
.tab-content { display: none; }
.tab-content.active { display: block; }

@media(max-width:767px){
    .tabs-desktop { display: none; }
    .tabs-mobile { display: block; }
}

@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#mobile-header h2{ color:#FFF; }
#content{ background-color:#121212; }
.info-card{ border:1px solid #333; background-color:#1E1E1E; }
h2,h3{ color:#BB86FC; }
.info-label { color:#BBB; }
.info-row { border-bottom:1px solid #333; }
.tabs-container { border-bottom-color: #333; }
.tab-link { background: #333; color: #BBB; }
.tab-link.active { background: #BB86FC; color: #000; }
.tabs-mobile { background: #1E1E1E; color: #E0E0E0; border-color: #444; }
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
    <a href="/messages"><i class="fa-solid fa-message"></i> Messages</a>
    <a href="/history.php"><i class="fa-solid fa-clock-rotate-left"></i> History</a>

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
      <a href="/admim/manage-groups.php"><i class="fa-solid fa-user-group"></i> Manage Groups</a>
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
            <a href="branding.php" class="tab-link">Branding</a>
            <a href="about.php" class="tab-link active">About</a>
        </div>
        <select class="tabs-mobile" onchange="window.location.href=this.value">
            <option value="general.php">General</option>
            <option value="login.php">Login</option>
            <option value="sip.php">SIP</option>
            <option value="branding.php" selected>Branding</option>
            <option value="about.php">About</option>
        </select>
    </div>

    <div id="about" class="tab-content active">
        <picture>
            <source srcset="/assets/OPENPAGINGSERVER-768x576-DARKMODE.png" media="(prefers-color-scheme: dark)">
            <img src="/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png" class="server-image">
        </picture>
        <p>Open Paging Server 0.1.0</p>
		<p>Open Paging Server is licensed under the GNU General Public License v2.0. Open Paging Server installs and uses several open-source software from their official sources without source code modifications. These components are subject to their own licenses.</p>
        <p>Open Paging Server is provided "as is" without any warranties, express or implied, including but not limited to fitness for a particular purpose or non-infringement.</p>
        <p></p>
        <div class="info-card">
            <h2>Hardware & OS</h2>
            <div class="info-row"><span class="info-label">Hostname</span><span><?php echo htmlspecialchars($sysInfo['hostname']); ?></span></div>
            <div class="info-row"><span class="info-label">Operating System</span><span><?php echo htmlspecialchars($sysInfo['os']); ?></span></div>
            <div class="info-row"><span class="info-label">Processor</span><span><?php echo htmlspecialchars($sysInfo['cpu']); ?></span></div>
            <?php if(isset($sysInfo['ram'])): ?>
            <div class="info-row"><span class="info-label">Total Memory</span><span><?php echo htmlspecialchars($sysInfo['ram']); ?></span></div>
            <?php endif; ?>
            <div class="info-row"><span class="info-label">System Uptime</span><span><?php echo htmlspecialchars($sysInfo['uptime']); ?></span></div>
        </div>

        <div class="info-card">
            <h2>Networking</h2>
            <?php if (!empty($netInfo['private_ipv4'])): ?>
                <div class="info-row"><span class="info-label">Private IPv4</span><span><?php echo htmlspecialchars(implode(', ', $netInfo['private_ipv4'])); ?></span></div>
                <div class="info-row"><span class="info-label">Public IPv4 (Detected)</span><span><?php echo htmlspecialchars($netInfo['public_detected'] ?? 'Unknown'); ?></span></div>
            <?php else: ?>
                <div class="info-row"><span class="info-label">Public IPv4</span><span><?php echo htmlspecialchars(implode(', ', $netInfo['public_ipv4'])); ?></span></div>
            <?php endif; ?>
            <div class="info-row"><span class="info-label">Gateway</span><span><?php echo htmlspecialchars($netInfo['gateway']); ?></span></div>
            <?php if (!empty($netInfo['dns'])): ?>
            <div class="info-row"><span class="info-label">DNS Servers</span><span><?php echo htmlspecialchars(implode(', ', $netInfo['dns'])); ?></span></div>
            <?php endif; ?>
            <?php if (!empty($netInfo['ipv6'])): ?>
            <div class="info-row"><span class="info-label">IPv6 Addresses</span><span><?php echo htmlspecialchars(implode(', ', $netInfo['ipv6'])); ?></span></div>
            <?php endif; ?>
        </div>

        <div class="info-card">
            <h2>Software Environment</h2>
            <div class="info-row"><span class="info-label">PHP Version</span><span><?php echo htmlspecialchars($sysInfo['php_version']); ?></span></div>
            <div class="info-row"><span class="info-label">Web Server</span><span><?php echo htmlspecialchars($_SERVER['SERVER_SOFTWARE']); ?></span></div>
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
</script>
</body>
</html>
