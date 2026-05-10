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

$scheduleId = (int)($_GET['schedule_id'] ?? $_POST['schedule_id'] ?? 0);
$schedule = bells_schedule($pdo, $scheduleId);

bells_handle_list_editor_post(
    $pdo,
    $scheduleId,
    '/bells/lists.php',
    ['schedule_id' => $scheduleId]
);

bells_begin_page($settings, $user, 'Custom Bells');
bells_page_header(
    'Custom Bells',
    $schedule['name'],
    '<a class="btn secondary" href="/bells/edit.php?id=' . htmlspecialchars((string)$scheduleId) . '"><i class="fa-solid fa-arrow-left"></i> Back</a>'
);
?>

<?php bells_schedule_settings_card($schedule, 'bells'); ?>

<div class="info-card">
    <div class="list-title">Schedule custom bell lists</div>
    <div class="list-meta">Use these when this schedule needs its own bell pattern instead of the system-wide bell lists.</div>
</div>

<?php bells_render_list_editor($pdo, $scheduleId, '/bells/lists.php', ['schedule_id' => $scheduleId], $schedule); ?>

<?php bells_end_page(); ?>
