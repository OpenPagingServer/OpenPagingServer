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

function frame_safe_name($value) {
    return preg_match('/^[A-Za-z0-9_-]+$/', (string)$value) === 1;
}

function endpoint_frame_success_redirect($message) {
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

function endpoint_frame_was_success($message, $error, $errors = null) {
    $messageText = trim((string)$message);
    $errorText = trim((string)$error);
    $hasErrors = is_array($errors) ? !empty($errors) : false;
    return $_SERVER['REQUEST_METHOD'] === 'POST' && $messageText !== '' && $errorText === '' && !$hasErrors;
}

$module = $_GET['module'] ?? '';
$type = $_GET['type'] ?? '';
if (!frame_safe_name($module) || ($type !== '' && !frame_safe_name($type))) {
    http_response_code(400);
    echo "Invalid endpoint form";
    exit;
}

$modulesRoot = realpath(__DIR__ . '/../../endpoint-modules');
$moduleDir = $modulesRoot ? realpath($modulesRoot . DIRECTORY_SEPARATOR . $module) : false;
if (!$modulesRoot || !$moduleDir || strpos($moduleDir, $modulesRoot) !== 0) {
    http_response_code(404);
    echo "Module not found";
    exit;
}

$registryPath = $moduleDir . DIRECTORY_SEPARATOR . 'endpoint-forms' . DIRECTORY_SEPARATOR . 'forms.php';
if (!is_file($registryPath)) {
    http_response_code(404);
    echo "Module has no endpoint forms";
    exit;
}

$forms = require $registryPath;
if (!is_array($forms)) {
    http_response_code(404);
    echo "Endpoint forms not found";
    exit;
}

$formsDir = realpath($moduleDir . DIRECTORY_SEPARATOR . 'endpoint-forms');
if (!$formsDir) {
    http_response_code(404);
    echo "Endpoint forms not found";
    exit;
}

$moduleIndexPath = realpath($formsDir . DIRECTORY_SEPARATOR . 'index.php');
if ($type === '') {
    if (!$moduleIndexPath || strpos($moduleIndexPath, $formsDir) !== 0) {
        http_response_code(404);
        echo "Module endpoint chooser not found";
        exit;
    }
    require $moduleIndexPath;
    exit;
}

if (!isset($forms[$type]) || !is_array($forms[$type])) {
    http_response_code(404);
    echo "Endpoint form not found";
    exit;
}

$formFile = $forms[$type]['file'] ?? '';
if (!frame_safe_name(pathinfo($formFile, PATHINFO_FILENAME)) || pathinfo($formFile, PATHINFO_EXTENSION) !== 'php') {
    http_response_code(400);
    echo "Invalid endpoint form file";
    exit;
}

$formPath = realpath($moduleDir . DIRECTORY_SEPARATOR . 'endpoint-forms' . DIRECTORY_SEPARATOR . $formFile);
if (!$formPath || !$formsDir || strpos($formPath, $formsDir) !== 0) {
    http_response_code(404);
    echo "Endpoint form file not found";
    exit;
}

$endpointForm = $forms[$type];
ob_start();
require $formPath;
$content = ob_get_clean();

$messageForRedirect = $message ?? '';
$errorForRedirect = $error ?? '';
$errorsForRedirect = $errors ?? null;
if (endpoint_frame_was_success($messageForRedirect, $errorForRedirect, $errorsForRedirect)) {
    endpoint_frame_success_redirect($messageForRedirect);
    exit;
}

echo $content;
