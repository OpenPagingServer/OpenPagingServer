<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);

session_start();
require_once __DIR__ . '/../config.php';

if (!isset($_SESSION['user_id'])) {
    http_response_code(401);
    echo "Not authenticated";
    exit;
}

$stmt = $pdo->prepare("SELECT role FROM users WHERE id = :id LIMIT 1");
$stmt->execute(['id' => $_SESSION['user_id']]);
$userRole = $stmt->fetchColumn();
if (!in_array($userRole, ['admin', 'tempadmin'], true)) {
    http_response_code(403);
    echo "Forbidden";
    exit;
}

function endpoint_action_frame_safe_name($value) {
    return preg_match('/^[A-Za-z0-9_-]+$/', (string)$value) === 1;
}

function endpoint_action_frame_success_redirect($message) {
    $_SESSION['endpoint_flash_success'] = (string)$message;
    $target = '/admin/manage-endpoints.php';
    ?>
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Endpoint saved</title></head>
<body>
<script>
window.top.location.href = <?= json_encode($target) ?>;
</script>
<p>Endpoint saved. <a target="_top" href="<?= htmlspecialchars($target, ENT_QUOTES, 'UTF-8') ?>">Return to Manage Endpoints</a>.</p>
</body>
</html>
<?php
}

function endpoint_action_frame_was_success($message, $error, $errors = null) {
    $messageText = trim((string)$message);
    $errorText = trim((string)$error);
    $hasErrors = is_array($errors) ? !empty($errors) : false;
    return $_SERVER['REQUEST_METHOD'] === 'POST' && $messageText !== '' && $errorText === '' && !$hasErrors;
}

$action = $_GET['action'] ?? '';
$module = $_GET['module'] ?? '';
$endpointId = trim((string)($_GET['id'] ?? ''));

if (!in_array($action, ['edit', 'delete'], true) || !endpoint_action_frame_safe_name($module) || $endpointId === '' || strlen($endpointId) > 255) {
    http_response_code(400);
    echo "Invalid endpoint action";
    exit;
}

$modulesRoot = realpath(__DIR__ . '/../../endpoint-modules');
$moduleDir = $modulesRoot ? realpath($modulesRoot . DIRECTORY_SEPARATOR . $module) : false;
if (!$modulesRoot || !$moduleDir || strpos($moduleDir, $modulesRoot) !== 0) {
    http_response_code(404);
    echo "Module not found";
    exit;
}

$actionPath = realpath($moduleDir . DIRECTORY_SEPARATOR . $action . '.php');
if (!$actionPath || strpos($actionPath, $moduleDir) !== 0 || !is_file($actionPath)) {
    http_response_code(404);
    echo "Module action not found";
    exit;
}

ob_start();
require $actionPath;
$content = ob_get_clean();

$messageForRedirect = $message ?? '';
$errorForRedirect = $error ?? '';
$errorsForRedirect = $errors ?? null;
if (endpoint_action_frame_was_success($messageForRedirect, $errorForRedirect, $errorsForRedirect)) {
    endpoint_action_frame_success_redirect($messageForRedirect);
    exit;
}

echo $content;
