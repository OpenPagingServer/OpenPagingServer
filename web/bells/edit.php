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

$scheduleId = (int)($_GET['id'] ?? $_POST['id'] ?? 0);
if ($scheduleId <= 0) {
    bells_redirect('/bells');
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = $_POST['action'] ?? 'save';
    if ($action === 'delete') {
        foreach (['bell_schedule_groups', 'bell_calendar', 'bell_calendar_lists', 'bell_lists'] as $table) {
            $stmt = $pdo->prepare("DELETE FROM `$table` WHERE schedule_id = :schedule_id");
            $stmt->execute(['schedule_id' => $scheduleId]);
        }
        $pdo->exec("DELETE FROM bell_events WHERE list_id NOT IN (SELECT id FROM bell_lists)");
        $stmt = $pdo->prepare("DELETE FROM bell_schedules WHERE id = :id");
        $stmt->execute(['id' => $scheduleId]);
        bells_redirect('/bells');
    }

    $name = trim($_POST['name'] ?? '');
    $enabled = isset($_POST['enabled']) ? 1 : 0;
    $timezone = trim($_POST['timezone'] ?? 'server');
    if ($timezone !== 'server' && !in_array($timezone, DateTimeZone::listIdentifiers(), true)) {
        $timezone = 'server';
    }
    if ($name !== '') {
        $stmt = $pdo->prepare("UPDATE bell_schedules SET name = :name, enabled = :enabled, timezone = :timezone WHERE id = :id");
        $stmt->execute(['name' => $name, 'enabled' => $enabled, 'timezone' => $timezone, 'id' => $scheduleId]);
    }
    $returnTo = trim($_POST['return_to'] ?? '');
    if ($returnTo !== '' && strpos($returnTo, '/bells/') === 0) {
        header("Location: " . $returnTo);
        exit;
    }
    bells_redirect('/bells/edit.php', ['id' => $scheduleId]);
}

$schedule = bells_schedule($pdo, $scheduleId);

bells_begin_page($settings, $user, 'Edit Bell Schedule');
bells_page_header(
    'Edit Schedule',
    $schedule['name'],
    '<a class="btn secondary" href="/bells"><i class="fa-solid fa-arrow-left"></i> Back</a>'
);
?>

<?php bells_schedule_settings_card($schedule, 'settings'); ?>

<form class="card" method="POST" action="/bells/edit.php" onsubmit="return confirm('Delete this schedule?')">
    <input type="hidden" name="id" value="<?= htmlspecialchars((string)$scheduleId) ?>">
    <input type="hidden" name="action" value="delete">
    <button class="btn danger" type="submit"><i class="fa-solid fa-trash"></i> Delete Schedule</button>
</form>

<?php bells_end_page(); ?>
