<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);

session_start();

$login_error = '';
$banner_enabled = false;
$banner_title = '';
$banner_message = '';
$product_name = 'Open Paging Server';
$login_logo_light = '';
$login_logo_dark = '';
$favicon = '';
$separate_dark_logo = false;
$enable_login_logo = false;

try {
    require_once __DIR__ . '/config.php';

    if (!isset($pdo)) {
        throw new Exception('PDO not set in config.php');
    }

    $stmt_settings = $pdo->prepare("SELECT parameter, value FROM systemsettings WHERE parameter IN ('login_banner_enabled', 'login_banner_title', 'login_banner_message', 'product_name', 'separate_dark_logo', 'login_logo_light', 'login_logo_dark', 'favicon', 'enable_login_logo')");
    $stmt_settings->execute();
    $settings = $stmt_settings->fetchAll(PDO::FETCH_KEY_PAIR);

    $banner_enabled = ($settings['login_banner_enabled'] ?? '0') === '1';
    $banner_title = $settings['login_banner_title'] ?? '';
    $banner_message = $settings['login_banner_message'] ?? '';
    $product_name = $settings['product_name'] ?? 'Open Paging Server';
    $separate_dark_logo = ($settings['separate_dark_logo'] ?? '0') === '1';
    $enable_login_logo = ($settings['enable_login_logo'] ?? '0') === '1';
    $login_logo_light = $settings['login_logo_light'] ?? '';
    $login_logo_dark = $settings['login_logo_dark'] ?? '';
    $favicon = $settings['favicon'] ?? '';

    $oobeTrigger = dirname(__DIR__) . '/.oobe';

    if (is_file($oobeTrigger)) {
        $stmt_user_count = $pdo->query("SELECT COUNT(*) FROM users");
        if ((int)$stmt_user_count->fetchColumn() === 0) {
            header("Location: /oobe");
            exit;
        }
    }
} catch (Throwable $e) {
    $login_error = 'Initialization failed: ' . $e->getMessage();
}

if (isset($_SESSION['user_id'])) {
    header("Location: dashboard.php");
    exit;
}

$ip = $_SERVER['REMOTE_ADDR'];
$ua = $_SERVER['HTTP_USER_AGENT'] ?? 'unknown';
$now = new DateTime();

$stmt = $pdo->prepare("CREATE TABLE IF NOT EXISTS login_attempts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ip VARCHAR(45),
    username VARCHAR(255),
    success TINYINT(1),
    attempt_time DATETIME,
    user_agent TEXT
) ENGINE=InnoDB");
$stmt->execute();

function getDelay(PDO $pdo, string $ip): int {
    $stmt = $pdo->prepare("SELECT attempt_time, success FROM login_attempts WHERE ip = :ip ORDER BY attempt_time DESC LIMIT 5");
    $stmt->execute(['ip' => $ip]);
    $attempts = $stmt->fetchAll(PDO::FETCH_ASSOC);

    $delay = 0;
    $base = 15;

    foreach ($attempts as $i => $a) {
        if ($a['success'] == 0) {
            if ($i < 3) {
                $delay = $base * pow(2, $i);
            } else {
                $delay = 60 * pow(3, $i - 3);
            }
            if ($delay > 3600) $delay = 3600;
        }
    }

    if ($delay > 0 && !empty($attempts)) {
        $lastAttempt = new DateTime($attempts[0]['attempt_time']);
        $remaining = $delay - ($GLOBALS['now']->getTimestamp() - $lastAttempt->getTimestamp());
        return max(0, $remaining);
    }

    return 0;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['get_challenge'])) {
    header('Content-Type: application/json');
    $delay = getDelay($pdo, $ip);

    if ($delay > 0) {
        echo json_encode(['success' => false, 'message' => "Too many login attempts. Try again in $delay seconds."]);
        exit;
    }

    try {
        $username = trim($_POST['username'] ?? '');
        $stmt = $pdo->prepare("SELECT salt FROM users WHERE username = :u1 OR email = :u2 LIMIT 1");
        $stmt->execute(['u1' => $username, 'u2' => $username]);
        $user = $stmt->fetch(PDO::FETCH_ASSOC);

        if ($user) {
            $challenge = bin2hex(random_bytes(32));
            $_SESSION['temp_challenge'] = $challenge;
            $_SESSION['temp_user'] = $username;

            $stmt = $pdo->prepare("INSERT INTO login_attempts (ip, username, success, attempt_time, user_agent) VALUES (:ip, :u, 0, NOW(), :ua)");
            $stmt->execute(['ip' => $ip, 'u' => $username, 'ua' => $ua]);

            echo json_encode(['success' => true, 'salt' => $user['salt'], 'challenge' => $challenge]);
        } else {
            $stmt = $pdo->prepare("INSERT INTO login_attempts (ip, username, success, attempt_time, user_agent) VALUES (:ip, :u, 0, NOW(), :ua)");
            $stmt->execute(['ip' => $ip, 'u' => $username, 'ua' => $ua]);

            echo json_encode(['success' => false, 'message' => 'Invalid username or password.']);
        }
    } catch (Throwable $e) {
        echo json_encode(['success' => false, 'message' => $e->getMessage()]);
    }

    exit;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['response'])) {
    header('Content-Type: application/json');
    $delay = getDelay($pdo, $ip);

    if ($delay > 0) {
        echo json_encode(['success' => false, 'message' => "Too many login attempts. Try again in $delay seconds."]);
        exit;
    }

    try {
        $clientResponse = $_POST['response'];
        $username = $_SESSION['temp_user'] ?? '';
        $challenge = $_SESSION['temp_challenge'] ?? '';

        $stmt = $pdo->prepare("SELECT id, username, password FROM users WHERE username = :u1 OR email = :u2 LIMIT 1");
        $stmt->execute(['u1' => $username, 'u2' => $username]);
        $user = $stmt->fetch(PDO::FETCH_ASSOC);

        if ($user && $challenge) {
            $expected = hash('sha256', $user['password'] . $challenge);

            if ($clientResponse === $expected) {
                session_regenerate_id(true);
                $_SESSION['user_id'] = $user['id'];
                $_SESSION['username'] = $user['username'];
                unset($_SESSION['temp_challenge'], $_SESSION['temp_user']);

                $stmt = $pdo->prepare("INSERT INTO login_attempts (ip, username, success, attempt_time, user_agent) VALUES (:ip, :u, 1, NOW(), :ua)");
                $stmt->execute(['ip' => $ip, 'u' => $username, 'ua' => $ua]);

                echo json_encode(['success' => true]);
            } else {
                $stmt = $pdo->prepare("INSERT INTO login_attempts (ip, username, success, attempt_time, user_agent) VALUES (:ip, :u, 0, NOW(), :ua)");
                $stmt->execute(['ip' => $ip, 'u' => $username, 'ua' => $ua]);

                echo json_encode(['success' => false, 'message' => 'Authentication failed.']);
            }
        } else {
            echo json_encode(['success' => false, 'message' => 'Session expired.']);
        }
    } catch (Throwable $e) {
        echo json_encode(['success' => false, 'message' => $e->getMessage()]);
    }

    exit;
}
?>
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Login - <?= htmlspecialchars($product_name) ?></title>
    <?php if (!empty($favicon)): ?>
    <link rel="icon" href="<?= htmlspecialchars($favicon) ?>" type="image/x-icon">
    <?php endif; ?>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet"/>
    <script src="https://cdn.jsdelivr.net/npm/js-sha256@0.9.0/src/sha256.min.js"></script>
    <style>
      body, html { margin: 0; padding: 0; font-family: "Tahoma", sans-serif; height: 100%; width: 100%; position: fixed; display: flex; align-items: center; justify-content: center; background: #e3f2fd; }
      @keyframes fadeInPage { from { opacity: 0; } to { opacity: 1; } }
      .background-slideshow { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; }
      @media (max-width: 768px) { .background-slideshow { display: none; } }
      .center-container { display: flex; flex-direction: column; justify-content: center; align-items: center; width: 100%; height: 100%; position: relative; z-index: 1; }
      .logo { position: fixed; top: 20px; left: 50%; transform: translateX(-50%); z-index: 2; width: 830px; height: 97px; display: flex; justify-content: center; align-items: center; }
      .logo img { max-width: 100%; max-height: 100%; object-fit: contain; }
      .logo-light { display: block; }
      .logo-dark { display: none; }
      @media (max-width: 768px) { .logo { width: 80%; height: auto; top: 10px; padding: 10px; } .logo img { width: 100%; height: auto; } }
      .login-banner { background: #fff3e0; border: 1px solid #ffe0b2; border-radius: 6px; padding: 15px; margin-bottom: 15px; width: 100%; max-width: 300px; box-sizing: border-box; text-align: left; color: #e65100; box-shadow: 0 2px 4px rgba(0,0,0,0.05); animation: fadeInPage 1s ease-in-out; }
      .login-banner h3 { margin: 0 0 5px 0; font-size: 15px; font-weight: 700; text-transform: uppercase; }
      .login-banner p { margin: 0; font-size: 14px; line-height: 1.4; }
      .login-box { background: #fff; padding: 30px; border-radius: 6px; box-shadow: 0 4px 6px rgba(0,0,0,0.1),0 1px 3px rgba(0,0,0,0.08); max-width: 300px; width: 50%; text-align: center; animation: fadeInPage 1.5s ease-in-out; }
      @media (max-width: 768px) { 
        .login-box { max-width: 100%; width: 100%; height: auto; border-radius: 0; padding: 20px; } 
        .login-banner { max-width: 90%; border-radius: 4px; }
        .center-container { padding: 0; align-items: center; } 
        body { background: #fff; } 
      }
      .login-box h2 { color: #1976d2; font-weight: 500; margin-bottom: 20px; margin-top: 0; }
      .input-field { position: relative; margin-bottom: 20px; }
      .input-field input { width: 100%; padding: 8px 0; border: none; border-bottom: 2px solid #ccc; font-size: 16px; background: transparent; outline: none; color: #333; font-family: "Roboto", sans-serif; }
      .input-field input:focus { border-bottom: 2px solid #1976d2; }
      .input-field label { position: absolute; top: 8px; left: 0; color: #888; font-size: 14px; pointer-events: none; transition: 0.2s ease all; }
      .input-field input:focus ~ label, .input-field input:not(:placeholder-shown) ~ label { top: -16px; left: 0; font-size: 12px; color: #1976d2; }
      .login-box button { width: 100%; padding: 12px; background-color: #1976d2; border: none; color: #fff; font-size: 16px; border-radius: 4px; cursor: pointer; font-family: "Roboto", sans-serif; text-transform: uppercase; position: relative; height: 45px; display: inline-flex; align-items: center; justify-content: center; }
      .login-box button.loading { pointer-events: none; background-color: #1565c0; }
      .loading-circle { width: 24px; height: 24px; border: 2px solid rgba(255,255,255,0.3); border-top: 2px solid #fff; border-radius: 50%; animation: spin 1s linear infinite; position: absolute; }
      @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      .error { color: #d32f2f; font-size: 0.9em; margin-top: 10px; min-height: 1.2em; }
      @media (prefers-color-scheme: dark) {
        body, html { background: #121212; color: #fff; }
        .login-banner { background: #3e2723; border: 1px solid #5d4037; color: #ffb74d; }
        .login-box { background: #1e1e1e; box-shadow: 0 4px 6px rgba(0,0,0,0.6); }
        .login-box h2 { color: #fff; }
        .input-field input { color: #fff; border-bottom: 2px solid #555; }
        .input-field label { color: #ccc; }
        .login-box button { background-color: #90caf9; color: #121212; }
        .error { color: #ffcdd2; }
        <?php if ($separate_dark_logo): ?>
        .logo-light { display: none; }
        .logo-dark { display: block; }
        <?php endif; ?>
      }
    </style>
  </head>
  <body>
    <div class="background-slideshow"></div>
    <?php if ($enable_login_logo): ?>
    <div class="logo">
      <?php if ($separate_dark_logo): ?>
        <img src="<?= htmlspecialchars($login_logo_light) ?>" alt="<?= htmlspecialchars($product_name) ?> logo" class="logo-light" />
        <img src="<?= htmlspecialchars($login_logo_dark) ?>" alt="<?= htmlspecialchars($product_name) ?> logo" class="logo-dark" />
      <?php else: ?>
        <img src="<?= htmlspecialchars($login_logo_light) ?>" alt="<?= htmlspecialchars($product_name) ?> logo" />
      <?php endif; ?>
    </div>
    <?php endif; ?>
    <div class="center-container">
      <?php if ($banner_enabled && (!empty($banner_title) || !empty($banner_message))): ?>
        <div class="login-banner">
          <?php if (!empty($banner_title)): ?>
            <h3><?= htmlspecialchars($banner_title) ?></h3>
          <?php endif; ?>
          <?php if (!empty($banner_message)): ?>
            <p><?= nl2br(htmlspecialchars($banner_message)) ?></p>
          <?php endif; ?>
        </div>
      <?php endif; ?>
      <div class="login-box">
        <h2>Login</h2>
        <div class="input-field">
          <input type="text" id="username" placeholder=" " required />
          <label for="username">Username or Email</label>
        </div>
        <div class="input-field">
          <input type="password" id="pw" placeholder=" " required />
          <label for="pw">Password</label>
        </div>
        <button id="login-button" onclick="startLogin()">Login</button>
        <p id="login-error" class="error"><?= htmlspecialchars($login_error) ?></p>
      </div>
    </div>
    <script>
      async function startLogin() {
        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('pw').value;
        const btn = document.getElementById('login-button');
        const err = document.getElementById('login-error');

        if (!username || !password) {
          err.innerText = 'Enter username and password';
          return;
        }

        err.innerText = '';
        btn.classList.add('loading');
        btn.innerHTML = '<div class="loading-circle"></div>';

        try {
          const res1 = await fetch('index.php', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams({ get_challenge: 1, username: username })
          });

          const data1 = await res1.json();

          if (!data1.success) throw new Error(data1.message);

          const verifier = sha256(password + data1.salt);
          const proof = sha256(verifier + data1.challenge);

          const res2 = await fetch('index.php', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams({ response: proof })
          });

          const data2 = await res2.json();

          if (data2.success) {
            window.location.href = 'dashboard.php';
          } else {
            throw new Error(data2.message || 'Verification failed');
          }
        } catch (e) {
          err.innerText = e.message;
          btn.classList.remove('loading');
          btn.innerHTML = 'Login';
        }
      }
    </script>
  </body>
</html>