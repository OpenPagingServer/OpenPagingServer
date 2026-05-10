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

$module = $_GET['module'] ?? '';
if (!frame_safe_name($module)) {
    http_response_code(400);
    echo "Invalid module";
    exit;
}

$modulesRoot = realpath(__DIR__ . '/../../endpoint-modules');
$moduleDir = $modulesRoot ? realpath($modulesRoot . DIRECTORY_SEPARATOR . $module) : false;
if (!$modulesRoot || !$moduleDir || strpos($moduleDir, $modulesRoot) !== 0) {
    http_response_code(404);
    echo "Module not found";
    exit;
}

$settingsPath = realpath($moduleDir . DIRECTORY_SEPARATOR . 'settings.php');
if (!$settingsPath || strpos($settingsPath, $moduleDir) !== 0) {
    ?>
    <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#666;background:#fff}@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#bbb}}</style></head><body>No module settings.</body></html>
    <?php
    exit;
}

require $settingsPath;
