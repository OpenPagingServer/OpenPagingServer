<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);

require_once __DIR__ . '/../config.php';
require_once __DIR__ . '/bell_helpers.php';

$user = bells_require_user($pdo);
$settings = bells_settings($pdo);
bells_ensure_schema($pdo);

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $name = trim($_POST['name'] ?? '');
    $enabled = isset($_POST['enabled']) ? 1 : 0;
    $timezone = trim($_POST['timezone'] ?? 'server');
    if ($timezone !== 'server' && !in_array($timezone, DateTimeZone::listIdentifiers(), true)) {
        $timezone = 'server';
    }
    if ($name !== '') {
        $stmt = $pdo->prepare("INSERT INTO bell_schedules (name, enabled, timezone) VALUES (:name, :enabled, :timezone)");
        $stmt->execute(['name' => $name, 'enabled' => $enabled, 'timezone' => $timezone]);
        bells_redirect('/bells/edit.php', ['id' => (int)$pdo->lastInsertId()]);
    }
}

bells_begin_page($settings, $user, 'New Bell Schedule');
bells_page_header(
    'New Schedule',
    'Create a bell schedule',
    '<a class="btn secondary" href="/bells"><i class="fa-solid fa-arrow-left"></i> Back</a>'
);
?>

<form class="card" method="POST" action="/bells/new.php">
    <div class="field">
        <label for="name">Schedule name</label>
        <input id="name" name="name" required autofocus>
    </div>
    <div class="field">
        <label for="timezone">Time zone</label>
        <select id="timezone" name="timezone">
            <?= bells_timezone_options('server') ?>
        </select>
    </div>
    <label class="checkbox-row">
        <input type="checkbox" name="enabled" checked>
        <span>Enabled</span>
    </label>
    <div class="actions" style="margin-top:12px;">
        <button class="btn" type="submit"><i class="fa-solid fa-floppy-disk"></i> Create Schedule</button>
        <a class="btn secondary" href="/bells">Cancel</a>
    </div>
</form>

<?php bells_end_page(); ?>
