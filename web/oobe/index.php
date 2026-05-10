<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);

session_start();
require_once __DIR__ . '/../config.php';

function oobe_h($value) {
    return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8');
}

function oobe_trigger_enabled() {
    $trigger = realpath(__DIR__ . '/../../.oobe');
    return $trigger && is_file($trigger) && filesize($trigger) === 0;
}

function oobe_user_count($pdo) {
    try {
        $stmt = $pdo->query("SELECT COUNT(*) FROM users");
        return (int)$stmt->fetchColumn();
    } catch (Throwable $exc) {
        return 1;
    }
}

function oobe_settings($pdo) {
    $defaults = [
        'product_name' => 'Open Paging Server',
        'favicon' => '',
        'separate_dark_logo' => '1',
        'enable_login_logo' => '1',
        'login_logo_light' => '/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png',
        'login_logo_dark' => '/assets/OPENPAGINGSERVER-768x576-DARKMODE.png',
    ];
    try {
        $stmt = $pdo->prepare("SELECT parameter, value FROM systemsettings WHERE parameter IN ('product_name','favicon','separate_dark_logo','enable_login_logo','login_logo_light','login_logo_dark')");
        $stmt->execute();
        foreach ($stmt->fetchAll(PDO::FETCH_KEY_PAIR) as $key => $value) {
            $defaults[$key] = $value;
        }
    } catch (Throwable $exc) {
    }
    return $defaults;
}

function oobe_save_setting($pdo, $parameter, $value, $description) {
    $stmt = $pdo->prepare(
        "INSERT INTO systemsettings (`parameter`, `value`, `description`) VALUES (:parameter, :value, :description) " .
        "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`), `description` = VALUES(`description`)"
    );
    $stmt->execute(['parameter' => $parameter, 'value' => $value, 'description' => $description]);
}

function oobe_hash_password($password) {
    $salt = bin2hex(random_bytes(16));
    return [hash('sha256', $password . $salt), $salt];
}

function oobe_stage() {
    $stage = $_POST['stage'] ?? $_GET['stage'] ?? 'welcome';
    return in_array($stage, ['welcome', 'account', 'time', 'modules', 'analytics', 'complete'], true) ? $stage : 'welcome';
}

function oobe_module_list() {
    $root = realpath(__DIR__ . '/../../endpoint-modules');
    if (!$root || !is_dir($root)) {
        return [];
    }
    $modules = [];
    foreach (scandir($root) as $entry) {
        if ($entry === '.' || $entry === '..' || !preg_match('/^[A-Za-z0-9_-]+$/', $entry)) {
            continue;
        }
        $infoPath = $root . DIRECTORY_SEPARATOR . $entry . DIRECTORY_SEPARATOR . 'info.xml';
        if (!is_file($infoPath)) {
            continue;
        }
        $xml = @simplexml_load_file($infoPath);
        if (!$xml) {
            continue;
        }
        $name = trim((string)($xml->name ?? $entry)) ?: $entry;
        $version = trim((string)($xml->version ?? ''));
        $author = trim((string)($xml->author ?? ''));
        $modules[] = ['name' => $name, 'version' => $version, 'author' => $author];
    }
    usort($modules, function ($a, $b) {
        return strtolower($a['name']) <=> strtolower($b['name']);
    });
    return $modules;
}

if (!oobe_trigger_enabled() || oobe_user_count($pdo) > 0) {
    header('Location: /');
    exit;
}

$settings = oobe_settings($pdo);
$stage = oobe_stage();
$error = '';
$notice = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = $_POST['action'] ?? 'next';
    if ($action === 'back') {
        $stage = $_POST['back_stage'] ?? 'welcome';
    } elseif ($stage === 'welcome') {
        $stage = 'account';
    } elseif ($stage === 'account') {
        $username = trim((string)($_POST['username'] ?? ''));
        $email = trim((string)($_POST['email'] ?? ''));
        $password = (string)($_POST['password'] ?? '');
        $confirm = (string)($_POST['confirm_password'] ?? '');
        if ($username === '') {
            $error = 'Username is required.';
        } elseif ($email !== '' && !filter_var($email, FILTER_VALIDATE_EMAIL)) {
            $error = 'Email must be blank or a valid address.';
        } elseif ($password === '') {
            $error = 'Password is required.';
        } elseif ($password !== $confirm) {
            $error = 'Password confirmation does not match.';
        } else {
            [$verifier, $salt] = oobe_hash_password($password);
            $_SESSION['oobe_user'] = [
                'username' => $username,
                'email' => $email,
                'password' => $verifier,
                'salt' => $salt,
            ];
            $stage = 'time';
        }
    } elseif ($stage === 'time') {
        $stage = 'modules';
    } elseif ($stage === 'modules') {
        $stage = 'analytics';
    } elseif ($stage === 'analytics') {
        $analytics = $action === 'opt_in' ? '1' : '0';
        oobe_save_setting($pdo, 'analytics', $analytics, 'Send optional analytics to the Open Paging Server project. Privacy Policy: https://www.openpagingserver.org/privacypolicy/analytics');
        $user = $_SESSION['oobe_user'] ?? null;
        if (!$user || empty($user['username']) || empty($user['password']) || empty($user['salt'])) {
            $stage = 'welcome';
            $error = 'Please create the administrator account first.';
        } elseif (oobe_user_count($pdo) === 0) {
            $pdo->exec("SET SESSION sql_mode = IF(FIND_IN_SET('NO_AUTO_VALUE_ON_ZERO', @@sql_mode), @@sql_mode, CONCAT_WS(',', @@sql_mode, 'NO_AUTO_VALUE_ON_ZERO'))");
            $stmt = $pdo->prepare("INSERT INTO users (id, username, email, password, salt, role, userperm, adminperm) VALUES (0, :username, :email, :password, :salt, 'admin', 'all', 'all')");
            $stmt->execute([
                'username' => $user['username'],
                'email' => $user['email'] !== '' ? $user['email'] : null,
                'password' => $user['password'],
                'salt' => $user['salt'],
            ]);
            unset($_SESSION['oobe_user']);
            $stage = 'complete';
        } else {
            header('Location: /');
            exit;
        }
    }
}

$now = new DateTime();
$timeString = $now->format('g:i A');
$dateString = $now->format('l M j, Y');
$timeIso = $now->format(DateTimeInterface::ATOM);
$modules = oobe_module_list();
$logoLight = $settings['login_logo_light'] ?? '';
$logoDark = $settings['login_logo_dark'] ?? '';
$separateDarkLogo = ($settings['separate_dark_logo'] ?? '0') === '1';
$showLogo = ($settings['enable_login_logo'] ?? '1') === '1';
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Setup - <?= oobe_h($settings['product_name']) ?></title>
<?php if (!empty($settings['favicon'])): ?><link rel="icon" href="<?= oobe_h($settings['favicon']) ?>" type="image/x-icon"><?php endif; ?>
<style>
body,html{margin:0;padding:0;min-height:100%;font-family:Tahoma,sans-serif;background:#e3f2fd;color:#202124}
.page{min-height:100vh;box-sizing:border-box;padding:28px;display:grid;grid-template-rows:auto 1fr;gap:28px}
.logo{width:280px;height:72px;display:flex;align-items:center}.logo img{max-width:100%;max-height:100%;object-fit:contain}.logo-dark{display:none}
.wrap{display:flex;align-items:center;justify-content:center}.card{background:#fff;border-radius:6px;box-shadow:0 4px 6px rgba(0,0,0,.1),0 1px 3px rgba(0,0,0,.08);padding:30px;max-width:560px;width:100%;box-sizing:border-box}
h1{color:#1976d2;font-weight:500;margin:0 0 12px}.lead{line-height:1.5;color:#424242}.field{position:relative;margin:22px 0}.field input,.field select{width:100%;box-sizing:border-box;padding:9px 0;border:0;border-bottom:2px solid #ccc;background:transparent;font-size:16px;outline:none;color:#333}.field input:focus,.field select:focus{border-bottom-color:#1976d2}.field label{display:block;color:#666;font-size:14px;margin-bottom:6px}.actions{display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap;margin-top:24px}.button{border:0;border-radius:4px;padding:12px 18px;background:#1976d2;color:#fff;font:inherit;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.button.secondary{background:#757575}.button.good{background:#2e7d32}.button.warn{background:#ef6c00}.error{background:#ffebee;border:1px solid #ef9a9a;color:#b71c1c;padding:10px;border-radius:6px;margin-bottom:14px}.notice{background:#e8f5e9;border:1px solid #a5d6a7;color:#1b5e20;padding:10px;border-radius:6px;margin-bottom:14px}.timebox{text-align:center;margin:24px 0}.time{font-size:40px;color:#1976d2}.date{font-size:18px;color:#555;margin-top:6px}.module-list{margin:18px 0 0;padding:0;list-style:none}.module-list li{padding:10px 0;border-bottom:1px solid #eee}.check{display:flex;gap:8px;align-items:center}.os-message{background:#fff3e0;border:1px solid #ffe0b2;color:#e65100;padding:10px;border-radius:6px}
@media(max-width:768px){.page{padding:18px;gap:12px}.logo{width:82%;height:auto;justify-self:center}.wrap{align-items:start}.card{border-radius:0;box-shadow:none;padding:22px;margin:0 -18px}.actions{justify-content:stretch}.button{flex:1}.time{font-size:34px}}
@media(prefers-color-scheme:dark){body,html{background:#121212;color:#e0e0e0}.card{background:#1e1e1e;box-shadow:0 4px 6px rgba(0,0,0,.6)}h1{color:#90caf9}.lead,.date{color:#bbb}.field input,.field select{color:#fff;border-bottom-color:#555}.field label{color:#ccc}.button{background:#90caf9;color:#121212}.button.secondary{background:#b0bec5}.button.good{background:#81c784}.button.warn{background:#ffb74d}.module-list li{border-bottom-color:#333}.notice{background:#14351a;border-color:#2e7d32;color:#c8e6c9}.error{background:#3b1515;border-color:#6d2a2a;color:#ffcdd2}.os-message{background:#3e2723;border-color:#5d4037;color:#ffb74d}<?php if ($separateDarkLogo): ?>.logo-light{display:none}.logo-dark{display:block}<?php endif; ?>}
</style>
</head>
<body>
<div class="page">
    <?php if ($showLogo): ?>
    <div class="logo">
        <?php if ($separateDarkLogo): ?>
            <img src="<?= oobe_h($logoLight) ?>" alt="<?= oobe_h($settings['product_name']) ?> logo" class="logo-light">
            <img src="<?= oobe_h($logoDark) ?>" alt="<?= oobe_h($settings['product_name']) ?> logo" class="logo-dark">
        <?php else: ?>
            <img src="<?= oobe_h($logoLight) ?>" alt="<?= oobe_h($settings['product_name']) ?> logo">
        <?php endif; ?>
    </div>
    <?php endif; ?>
    <main class="wrap">
        <section class="card">
            <?php if ($error): ?><div class="error"><?= oobe_h($error) ?></div><?php endif; ?>
            <?php if ($notice): ?><div class="notice"><?= oobe_h($notice) ?></div><?php endif; ?>

            <?php if ($stage === 'welcome'): ?>
                <h1>Welcome to Open Paging Server</h1>
                <p class="lead">You are a few steps away from getting started with your new paging system.</p>
                <form method="post">
                    <input type="hidden" name="stage" value="welcome">
                    <div class="actions"><button class="button good" type="submit">Start</button></div>
                </form>
            <?php elseif ($stage === 'account'): ?>
                <h1>Create an account</h1>
                <p class="lead">To begin, please create your user account. This will be the main administrator account, and cannot be deleted.</p>
                <form method="post">
                    <input type="hidden" name="stage" value="account">
                    <div class="field"><label>Username</label><input name="username" required autocomplete="username"></div>
                    <div class="field"><label>Email (optional)</label><input type="email" name="email" autocomplete="email"></div>
                    <div class="field"><label>Password</label><input type="password" name="password" required autocomplete="new-password"></div>
                    <div class="field"><label>Confirm Password</label><input type="password" name="confirm_password" required autocomplete="new-password"></div>
                    <div class="actions">
                        <button class="button secondary" name="action" value="back" type="submit">Back</button>
                        <input type="hidden" name="back_stage" value="welcome">
                        <button class="button" type="submit">Next</button>
                    </div>
                </form>
            <?php elseif ($stage === 'time'): ?>
                <h1>Is this date and time correct?</h1>
                <p class="lead">If not, ensure this system is using the correct NTP server and timezone. Correct date &amp; time is important for bells, scheduled broadcasts, history, message expiration, and general housekeeping.</p>
                <div class="timebox">
                    <div class="time" id="serverTime" data-iso="<?= oobe_h($timeIso) ?>"><?= oobe_h($timeString) ?></div>
                    <div class="date" id="serverDate" data-iso="<?= oobe_h($timeIso) ?>"><?= oobe_h($dateString) ?></div>
                </div>
                <form method="post" class="actions">
                    <input type="hidden" name="stage" value="time">
                    <button class="button secondary" name="action" value="back" type="submit">Back</button>
                    <input type="hidden" name="back_stage" value="account">
                    <button class="button" name="action" value="next" type="submit">Next</button>
                </form>
            <?php elseif ($stage === 'modules'): ?>
                <h1>Endpoint modules</h1>
                <p class="lead">Open Paging Server uses endpoint modules. You have the following endpoint modules installed:</p>
                <ul class="module-list">
                    <?php foreach ($modules as $module): ?>
                        <li><?= oobe_h($module['name']) ?><?= $module['version'] !== '' ? ' ' . oobe_h($module['version']) : '' ?><?= $module['author'] !== '' ? ' by ' . oobe_h($module['author']) : '' ?></li>
                    <?php endforeach; ?>
                    <?php if (!$modules): ?><li>No endpoint modules found.</li><?php endif; ?>
                </ul>
                <p class="lead">You can add more in /opt/openpagingserver/endpoint-modules</p>
                <form method="post" class="actions">
                    <input type="hidden" name="stage" value="modules">
                    <button class="button secondary" name="action" value="back" type="submit">Back</button>
                    <input type="hidden" name="back_stage" value="time">
                    <button class="button" type="submit">Next</button>
                </form>
            <?php elseif ($stage === 'analytics'): ?>
                <h1>Would you like to enable optional analytics?</h1>
                <p class="lead">To help the Open Paging Server project improve, you can opt-in to share optional analytics. Analytics contain mainly anonymous data such as your operating system, software versions, anonymized crash logs, etc. And may include your public IP address. You can change this setting later.</p>
                <form method="post" class="actions">
                    <input type="hidden" name="stage" value="analytics">
                    <button class="button secondary" name="action" value="back" type="submit">Back</button>
                    <input type="hidden" name="back_stage" value="modules">
                    <button class="button good" name="action" value="continue_disabled" type="submit">Continue disabled</button>
                    <button class="button" name="action" value="opt_in" type="submit">Opt-in</button>
                </form>
            <?php else: ?>
                <h1>Setup complete!</h1>
                <p class="lead">To continue, login with your username and password you just made.</p>
                <p class="lead">Happy Paging!</p>
                <div class="actions"><a class="button" href="/">Login</a></div>
            <?php endif; ?>
        </section>
    </main>
</div>
<script>
const serverTime = document.getElementById('serverTime');
const serverDate = document.getElementById('serverDate');
if (serverTime && serverDate) {
  const iso = serverTime.dataset.iso;
  const serverNow = new Date(iso);
  if (!Number.isNaN(serverNow.getTime())) {
    serverTime.textContent = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(serverNow);
    serverDate.textContent = new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric' }).format(serverNow);
  }
}
</script>
</body>
</html>
