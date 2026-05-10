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


function is_admin_role($role) {
    return $role === 'admin' || $role === 'tempadmin';
}

function role_options() {
    return [
        'admin' => 'Administrator',
        'tempadmin' => 'Temporary Administrator',
        'user' => 'User',
        'tempuser' => 'Temporary User',
        'receiver' => 'Receiver',
        'tempreceiver' => 'Temporary Receiver',
    ];
}

function role_label($role) {
    $roles = role_options();
    return $roles[$role] ?? ucfirst((string)$role);
}

function format_date_value($value) {
    if (empty($value) || $value === '0000-00-00') {
        return 'Never';
    }

    $dt = DateTime::createFromFormat('Y-m-d', (string)$value);
    if ($dt instanceof DateTime) {
        return $dt->format('M j, Y');
    }

    return (string)$value;
}

function format_datetime_value($value) {
    if (empty($value) || $value === '0000-00-00 00:00:00') {
        return 'Never';
    }

    $dt = DateTime::createFromFormat('Y-m-d H:i:s', (string)$value);
    if ($dt instanceof DateTime) {
        return $dt->format('M j, Y g:i A');
    }

    return (string)$value;
}

function valid_date_string($value) {
    if ($value === '') {
        return true;
    }

    $dt = DateTime::createFromFormat('Y-m-d', $value);
    return $dt instanceof DateTime && $dt->format('Y-m-d') === $value;
}

function hash_password_value($password) {
    $salt = bin2hex(random_bytes(16));
    $verifier = hash('sha256', $password . $salt);
    return [$verifier, $salt];
}

function admin_count($pdo) {
    $stmt = $pdo->query("SELECT COUNT(*) FROM users WHERE role IN ('admin', 'tempadmin')");
    return (int)$stmt->fetchColumn();
}

function fetch_users_with_login_stats($pdo) {
    $sql = "
        SELECT
            u.id,
            u.username,
            u.email,
            u.role,
            u.loginsleft,
            u.accountexpire,
            u.accountcreated,
            COALESCE(ls.logincount, 0) AS logincount,
            ls.lastlogin
        FROM users u
        LEFT JOIN (
            SELECT
                u2.id AS user_id,
                COUNT(la.id) AS logincount,
                MAX(la.attempt_time) AS lastlogin
            FROM users u2
            LEFT JOIN login_attempts la
                ON la.success = 1
               AND (
                    la.username = u2.username
                    OR (u2.email IS NOT NULL AND u2.email <> '' AND la.username = u2.email)
               )
            GROUP BY u2.id
        ) ls ON ls.user_id = u.id
        ORDER BY u.username ASC
    ";

    $stmt = $pdo->query($sql);
    return $stmt->fetchAll(PDO::FETCH_ASSOC);
}

function fetch_user_with_login_stats($pdo, $userId) {
    $stmt = $pdo->prepare(
        "
        SELECT
            u.id,
            u.username,
            u.email,
            u.role,
            u.loginsleft,
            u.accountexpire,
            u.accountcreated,
            COALESCE(COUNT(la.id), 0) AS logincount,
            MAX(la.attempt_time) AS lastlogin
        FROM users u
        LEFT JOIN login_attempts la
            ON la.success = 1
           AND (
                la.username = u.username
                OR (u.email IS NOT NULL AND u.email <> '' AND la.username = u.email)
           )
        WHERE u.id = :id
        GROUP BY u.id, u.username, u.email, u.role, u.loginsleft, u.accountexpire, u.accountcreated
        LIMIT 1
        "
    );
    $stmt->execute(['id' => $userId]);
    return $stmt->fetch(PDO::FETCH_ASSOC);
}

$flash = $_SESSION['manage_users_flash'] ?? null;
unset($_SESSION['manage_users_flash']);

$roleOptions = role_options();
$formError = '';
$editUser = null;
$showEditor = false;

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = $_POST['action'] ?? '';

    if ($action === 'delete') {
        $userId = (int)($_POST['user_id'] ?? 0);

        $stmt = $pdo->prepare("SELECT id, username, role FROM users WHERE id = :id LIMIT 1");
        $stmt->execute(['id' => $userId]);
        $targetUser = $stmt->fetch(PDO::FETCH_ASSOC);

        if (!$targetUser) {
            $_SESSION['manage_users_flash'] = ['type' => 'error', 'message' => 'User not found.'];
        } elseif ((int)$targetUser['id'] === 0) {
            $_SESSION['manage_users_flash'] = ['type' => 'error', 'message' => 'User ID 0 cannot be deleted.'];
        } elseif ((int)$targetUser['id'] === (int)$_SESSION['user_id']) {
            $_SESSION['manage_users_flash'] = ['type' => 'error', 'message' => 'You cannot delete the account you are currently signed in with.'];
        } elseif (is_admin_role($targetUser['role']) && admin_count($pdo) <= 1) {
            $_SESSION['manage_users_flash'] = ['type' => 'error', 'message' => 'At least one administrator must remain on the server.'];
        } else {
            $stmt = $pdo->prepare("DELETE FROM users WHERE id = :id");
            $stmt->execute(['id' => $userId]);
            $_SESSION['manage_users_flash'] = ['type' => 'success', 'message' => 'User deleted.'];
        }

        header("Location: /admin/manage-users.php");
        exit;
    }

    if ($action === 'save') {
        $userIdRaw = trim($_POST['user_id'] ?? '');
        $userId = $userIdRaw === '' ? null : (int)$userIdRaw;
        $username = trim($_POST['username'] ?? '');
        $email = trim($_POST['email'] ?? '');
        $role = trim($_POST['role'] ?? '');
        $password = (string)($_POST['password'] ?? '');
        $confirmPassword = (string)($_POST['confirm_password'] ?? '');
        $accountExpire = trim($_POST['accountexpire'] ?? '');
        $loginsLeftRaw = trim($_POST['loginsleft'] ?? '0');
        $loginsLeft = $loginsLeftRaw === '' ? 0 : max(0, (int)$loginsLeftRaw);

        $editUser = [
            'id' => $userId,
            'username' => $username,
            'email' => $email,
            'role' => $role,
            'accountexpire' => $accountExpire,
            'loginsleft' => $loginsLeft,
            'logincount' => 0,
            'lastlogin' => null,
            'accountcreated' => date('Y-m-d'),
        ];
        $showEditor = true;

        if ($username === '') {
            $formError = 'Username is required.';
        } elseif (!array_key_exists($role, $roleOptions)) {
            $formError = 'Please choose a valid role.';
        } elseif ($email !== '' && !filter_var($email, FILTER_VALIDATE_EMAIL)) {
            $formError = 'Email must be blank or a valid address.';
        } elseif (!valid_date_string($accountExpire)) {
            $formError = 'Account expiration must use the YYYY-MM-DD format.';
        } elseif ($userId === null && $password === '') {
            $formError = 'Password is required when creating a user.';
        } elseif ($password !== '' && $password !== $confirmPassword) {
            $formError = 'Password confirmation does not match.';
        }

        $existingUser = null;
        if ($formError === '' && $userId !== null) {
            $existingUser = fetch_user_with_login_stats($pdo, $userId);

            if (!$existingUser) {
                $formError = 'User not found.';
            } else {
                $editUser['logincount'] = $existingUser['logincount'] ?? 0;
                $editUser['lastlogin'] = $existingUser['lastlogin'] ?? null;
                $editUser['accountcreated'] = $existingUser['accountcreated'] ?? null;
            }
        }

        if ($formError === '' && $userId !== null && (int)$userId === (int)$_SESSION['user_id'] && !is_admin_role($role)) {
            $formError = 'You cannot remove admin access from the account you are currently using.';
        }

        if (
            $formError === '' &&
            $userId !== null &&
            $existingUser &&
            is_admin_role($existingUser['role']) &&
            !is_admin_role($role) &&
            admin_count($pdo) <= 1
        ) {
            $formError = 'At least one administrator must remain on the server.';
        }

        if ($formError === '') {
            try {
                $emailValue = $email === '' ? null : $email;
                $expireValue = $accountExpire === '' ? null : $accountExpire;

                if ($userId === null) {
                    [$passwordHash, $salt] = hash_password_value($password);
                    $stmt = $pdo->prepare(
                        "INSERT INTO users (username, email, password, salt, role, loginsleft, accountexpire)
                         VALUES (:username, :email, :password, :salt, :role, :loginsleft, :accountexpire)"
                    );
                    $stmt->execute([
                        'username' => $username,
                        'email' => $emailValue,
                        'password' => $passwordHash,
                        'salt' => $salt,
                        'role' => $role,
                        'loginsleft' => $loginsLeft,
                        'accountexpire' => $expireValue,
                    ]);
                    $_SESSION['manage_users_flash'] = ['type' => 'success', 'message' => 'User created.'];
                } else {
                    $params = [
                        'id' => $userId,
                        'username' => $username,
                        'email' => $emailValue,
                        'role' => $role,
                        'loginsleft' => $loginsLeft,
                        'accountexpire' => $expireValue,
                    ];

                    if ($password !== '') {
                        [$passwordHash, $salt] = hash_password_value($password);
                        $stmt = $pdo->prepare(
                            "UPDATE users
                             SET username = :username,
                                 email = :email,
                                 role = :role,
                                 loginsleft = :loginsleft,
                                 accountexpire = :accountexpire,
                                 password = :password,
                                 salt = :salt
                             WHERE id = :id"
                        );
                        $params['password'] = $passwordHash;
                        $params['salt'] = $salt;
                    } else {
                        $stmt = $pdo->prepare(
                            "UPDATE users
                             SET username = :username,
                                 email = :email,
                                 role = :role,
                                 loginsleft = :loginsleft,
                                 accountexpire = :accountexpire
                             WHERE id = :id"
                        );
                    }

                    $stmt->execute($params);
                    $_SESSION['manage_users_flash'] = ['type' => 'success', 'message' => 'User updated.'];
                }

                header("Location: /admin/manage-users.php");
                exit;
            } catch (Throwable $e) {
                if ($e instanceof PDOException && (int)$e->getCode() === 23000) {
                    $formError = 'That username or email address is already in use.';
                } else {
                    $formError = 'Unable to save user right now.';
                }
            }
        }
    }
}

$users = fetch_users_with_login_stats($pdo);

$adminUsers = 0;
foreach ($users as $user) {
    if (is_admin_role($user['role'])) {
        $adminUsers++;
    }
}

if (!$showEditor) {
    $editUserId = $_GET['edit'] ?? '';
    if ($editUserId !== '') {
        foreach ($users as $user) {
            if ((string)$user['id'] === (string)$editUserId) {
                $editUser = $user;
                $showEditor = true;
                break;
            }
        }
    } elseif (isset($_GET['new'])) {
        $editUser = [
            'id' => '',
            'username' => '',
            'email' => '',
            'role' => 'user',
            'loginsleft' => 0,
            'logincount' => 0,
            'lastlogin' => null,
            'accountexpire' => '',
            'accountcreated' => date('Y-m-d'),
        ];
        $showEditor = true;
    }
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Manage Users - <?= htmlspecialchars($product_name) ?></title>
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
.header-actions h1 { margin:0; }
.card { background:#FFF; border:1px solid #EEE; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); padding:16px; }
.card h2 { margin:0 0 14px 0; font-size:1.1em; font-weight:500; color:#1976D2; }
.summary-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,220px)); gap:12px; margin-bottom:18px; }
.summary-item { border:1px solid #EEE; border-radius:8px; padding:12px; background:#FFF; box-shadow:0 2px 4px rgba(0,0,0,0.08); }
.summary-item strong { display:block; font-size:1.4em; font-weight:500; }
.field-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }
.field { display:flex; flex-direction:column; gap:6px; margin-bottom:14px; }
.field label { color:#555; font-size:0.9em; }
.field input, .field select { border:1px solid #CCC; border-radius:4px; padding:10px; font:inherit; box-sizing:border-box; background:#FFF; }
.hint { color:#777; font-size:0.88em; margin-top:-8px; margin-bottom:12px; }
.btn-primary { background:#1976D2; color:#FFF; border:none; border-radius:4px; padding:10px 14px; cursor:pointer; font:inherit; text-decoration:none; display:inline-flex; align-items:center; gap:8px; }
.btn-secondary { color:#1976D2; text-decoration:none; padding:10px 12px; display:inline-flex; align-items:center; }
.user-list { list-style:none; margin:0; padding:0; }
.user-item { display:flex; justify-content:space-between; gap:14px; padding:14px 0; border-bottom:1px solid #EEE; }
.user-item:last-child { border-bottom:none; }
.user-main { flex:1; min-width:0; }
.user-name-row { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.user-name { font-weight:500; color:#202124; overflow-wrap:anywhere; }
.user-meta { color:#666; font-size:0.9em; margin-top:4px; overflow-wrap:anywhere; }
.user-stats { color:#777; font-size:0.88em; margin-top:6px; display:flex; flex-wrap:wrap; gap:10px; }
.group-actions { display:flex; align-items:center; gap:4px; }
.icon-action { width:36px; height:36px; border-radius:50%; color:#555; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; border:none; background:transparent; cursor:pointer; }
.icon-action:hover { background:rgba(25,118,210,0.08); color:#1976D2; }
.icon-action.delete:hover { background:rgba(198,40,40,0.08); color:#C62828; }
.role-badge { display:inline-flex; align-items:center; padding:4px 8px; border-radius:999px; background:#E3F2FD; color:#1565C0; font-size:0.8em; font-weight:500; }
.admin-badge { background:#FFF3E0; color:#E65100; }
.flash, .error { padding:12px; border-radius:8px; margin-bottom:16px; }
.flash.success { background:#E8F5E9; border:1px solid #A5D6A7; color:#1B5E20; }
.flash.error, .error { background:#FFEBEE; border:1px solid #EF9A9A; color:#B71C1C; }
.muted { color:#777; font-size:0.9em; }
.editor-card { margin-top:18px; }
.form-actions { display:flex; align-items:center; gap:12px; margin-top:8px; flex-wrap:wrap; }
.inline-note { font-size:0.9em; color:#666; }
@media(max-width:767px){ .header-actions{ align-items:flex-start; flex-direction:column; } .user-item{ align-items:flex-start; flex-direction:column; } .group-actions{ margin-top:4px; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E0E0E0; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.card,.summary-item{ border:1px solid #333; background-color:#1E1E1E; }
.card h2 { color:#BB86FC; }
.field label,.muted,.hint,.inline-note,.user-meta,.user-stats{ color:#BBB; }
.field input,.field select { background:#121212; border-color:#444; color:#E0E0E0; }
.btn-primary { background:#BB86FC; color:#000; }
.btn-secondary { color:#BB86FC; }
.user-item { border-bottom:1px solid #333; }
.user-name { color:#EDEDED; }
.icon-action { color:#BBB; }
.icon-action:hover { background:rgba(187,134,252,0.1); color:#BB86FC; }
.icon-action.delete:hover { background:rgba(244,67,54,0.12); color:#EF9A9A; }
.role-badge { background:#2D2340; color:#D8C2FF; }
.admin-badge { background:#3A2B1B; color:#FFCC80; }
.flash.success { background:#12301A; border-color:#2E7D32; color:#C8E6C9; }
.flash.error,.error { background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
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
    <a href="/admin/manage-users.php" class="active admin-only"><i class="fa-solid fa-users-cog"></i> Manage Users</a>
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
        <h1><?= $showEditor ? (($editUser && !empty($editUser['id'])) ? 'Edit User' : 'New User') : 'Manage Users' ?></h1>
        <?php if ($showEditor): ?>
            <a class="btn-secondary" href="/admin/manage-users.php"><i class="fa-solid fa-arrow-left"></i> Back</a>
        <?php else: ?>
            <a class="btn-primary" href="/admin/manage-users.php?new=1"><i class="fa-solid fa-plus"></i> New User</a>
        <?php endif; ?>
    </div>

    <?php if ($flash): ?>
        <div class="flash <?= htmlspecialchars($flash['type'] ?? 'success') ?>"><?= htmlspecialchars($flash['message'] ?? '') ?></div>
    <?php endif; ?>
    <?php if ($formError !== ''): ?>
        <div class="error"><?= htmlspecialchars($formError) ?></div>
    <?php endif; ?>

    <?php if (!$showEditor): ?>
        <div class="summary-grid">
            <div class="summary-item">
                <strong><?= htmlspecialchars((string)count($users)) ?></strong>
                <span class="muted">Users</span>
            </div>
            <div class="summary-item">
                <strong><?= htmlspecialchars((string)$adminUsers) ?></strong>
                <span class="muted">Administrators</span>
            </div>
        </div>

        <div class="card">
            <h2>Users</h2>
            <?php if (empty($users)): ?>
                <p class="muted">No users found.</p>
            <?php else: ?>
                <ul class="user-list">
                    <?php foreach ($users as $user): ?>
                        <?php
                        $userRoleClass = is_admin_role($user['role']) ? 'role-badge admin-badge' : 'role-badge';
                        $emailDisplay = trim((string)($user['email'] ?? '')) === '' ? 'No email address' : $user['email'];
                        $canDelete = (int)$user['id'] !== 0 && (int)$user['id'] !== (int)$_SESSION['user_id'] && !(is_admin_role($user['role']) && $adminUsers <= 1);
                        ?>
                        <li class="user-item">
                            <div class="user-main">
                                <div class="user-name-row">
                                    <div class="user-name"><?= htmlspecialchars($user['username'] ?? '') ?></div>
                                    <span class="<?= htmlspecialchars($userRoleClass) ?>"><?= htmlspecialchars(role_label($user['role'] ?? '')) ?></span>
                                </div>
                                <div class="user-meta"><?= htmlspecialchars($emailDisplay) ?></div>
                                <div class="user-stats">
                                    <span>Created: <?= htmlspecialchars(format_date_value($user['accountcreated'] ?? '')) ?></span>
                                    <span>Last login: <?= htmlspecialchars(format_datetime_value($user['lastlogin'] ?? '')) ?></span>
                                    <span>Uses left: <?= htmlspecialchars((string)($user['loginsleft'] ?? 0)) ?></span>
                                    <span>Login count: <?= htmlspecialchars((string)($user['logincount'] ?? 0)) ?></span>
                                    <span>Expires: <?= htmlspecialchars(format_date_value($user['accountexpire'] ?? '')) ?></span>
                                </div>
                            </div>
                            <div class="group-actions">
                                <a class="icon-action" href="/admin/manage-users.php?edit=<?= urlencode((string)$user['id']) ?>" title="Edit"><i class="fa-solid fa-pen-to-square"></i></a>
                                <?php if ($canDelete): ?>
                                    <form method="POST" action="/admin/manage-users.php" onsubmit="return confirm('Delete this user?')">
                                        <input type="hidden" name="action" value="delete">
                                        <input type="hidden" name="user_id" value="<?= htmlspecialchars((string)$user['id']) ?>">
                                        <button class="icon-action delete" type="submit" title="Delete"><i class="fa-solid fa-trash"></i></button>
                                    </form>
                                <?php endif; ?>
                            </div>
                        </li>
                    <?php endforeach; ?>
                </ul>
            <?php endif; ?>
        </div>
    <?php else: ?>
        <form class="card editor-card" method="POST" action="/admin/manage-users.php">
            <h2><?= !empty($editUser['id']) ? 'Edit User' : 'New User' ?></h2>
            <input type="hidden" name="action" value="save">
            <input type="hidden" name="user_id" value="<?= htmlspecialchars((string)($editUser['id'] ?? '')) ?>">

            <div class="field-grid">
                <div class="field">
                    <label for="username">Username</label>
                    <input id="username" name="username" value="<?= htmlspecialchars((string)($editUser['username'] ?? '')) ?>" required>
                </div>
                <div class="field">
                    <label for="email">Email</label>
                    <input id="email" name="email" type="email" value="<?= htmlspecialchars((string)($editUser['email'] ?? '')) ?>" placeholder="Optional">
                </div>
                <div class="field">
                    <label for="role">Role</label>
                    <select id="role" name="role" required>
                        <?php foreach ($roleOptions as $roleValue => $roleText): ?>
                            <option value="<?= htmlspecialchars($roleValue) ?>" <?= ($editUser['role'] ?? '') === $roleValue ? 'selected' : '' ?>><?= htmlspecialchars($roleText) ?></option>
                        <?php endforeach; ?>
                    </select>
                </div>
                <div class="field">
                    <label for="loginsleft">Uses Left</label>
                    <input id="loginsleft" name="loginsleft" type="number" min="0" value="<?= htmlspecialchars((string)($editUser['loginsleft'] ?? 0)) ?>">
                </div>
                <div class="field">
                    <label for="accountexpire">Account Expires</label>
                    <input id="accountexpire" name="accountexpire" type="date" value="<?= htmlspecialchars((string)($editUser['accountexpire'] ?? '')) ?>">
                </div>
            </div>

            <div class="field-grid">
                <div class="field">
                    <label for="password"><?= !empty($editUser['id']) ? 'New Password' : 'Password' ?></label>
                    <input id="password" name="password" type="password" <?= empty($editUser['id']) ? 'required' : '' ?>>
                </div>
                <div class="field">
                    <label for="confirm_password"><?= !empty($editUser['id']) ? 'Confirm New Password' : 'Confirm Password' ?></label>
                    <input id="confirm_password" name="confirm_password" type="password" <?= empty($editUser['id']) ? 'required' : '' ?>>
                </div>
            </div>

            <?php if (!empty($editUser['id'])): ?>
                <div class="hint">Leave the password fields blank to keep the current password.</div>
                <div class="inline-note">
                    Created: <?= htmlspecialchars(format_date_value($editUser['accountcreated'] ?? '')) ?> |
                    Last login: <?= htmlspecialchars(format_datetime_value($editUser['lastlogin'] ?? '')) ?> |
                    Login count: <?= htmlspecialchars((string)($editUser['logincount'] ?? 0)) ?>
                </div>
            <?php endif; ?>

            <div class="form-actions">
                <button class="btn-primary" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save User</button>
                <a class="btn-secondary" href="/admin/manage-users.php">Cancel</a>
            </div>
        </form>
    <?php endif; ?>
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
