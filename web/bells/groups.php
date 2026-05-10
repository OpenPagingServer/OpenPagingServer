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

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $groups = $_POST['groups'] ?? [];
    if (!is_array($groups)) {
        $groups = [];
    }
    $groups = array_values(array_unique(array_filter(array_map('trim', $groups))));

    $stmt = $pdo->prepare("DELETE FROM bell_schedule_groups WHERE schedule_id = :schedule_id");
    $stmt->execute(['schedule_id' => $scheduleId]);
    $stmt = $pdo->prepare("INSERT INTO bell_schedule_groups (schedule_id, group_id) VALUES (:schedule_id, :group_id)");
    foreach ($groups as $groupId) {
        $stmt->execute(['schedule_id' => $scheduleId, 'group_id' => $groupId]);
    }
    bells_redirect('/bells/groups.php', ['schedule_id' => $scheduleId]);
}

$stmt = $pdo->query("SELECT id, name FROM groups ORDER BY name ASC");
$groups = $stmt->fetchAll(PDO::FETCH_ASSOC);

$stmt = $pdo->prepare("SELECT group_id FROM bell_schedule_groups WHERE schedule_id = :schedule_id");
$stmt->execute(['schedule_id' => $scheduleId]);
$selectedGroups = array_map('strval', $stmt->fetchAll(PDO::FETCH_COLUMN));

bells_begin_page($settings, $user, 'Bell Schedule Groups');
bells_page_header(
    'Schedule Groups',
    $schedule['name'],
    '<a class="btn secondary" href="/bells/edit.php?id=' . htmlspecialchars((string)$scheduleId) . '"><i class="fa-solid fa-arrow-left"></i> Back</a>'
);
?>

<?php bells_schedule_settings_card($schedule, 'groups'); ?>

<form class="card" method="POST" action="/bells/groups.php">
    <input type="hidden" name="schedule_id" value="<?= htmlspecialchars((string)$scheduleId) ?>">
    <?php if (empty($groups)): ?>
        <p class="muted">No groups are available.</p>
    <?php else: ?>
        <?php foreach ($groups as $group): ?>
            <label class="checkbox-row">
                <input type="checkbox" name="groups[]" value="<?= htmlspecialchars($group['id']) ?>" <?= in_array((string)$group['id'], $selectedGroups, true) ? 'checked' : '' ?>>
                <span><?= htmlspecialchars($group['name']) ?></span>
            </label>
        <?php endforeach; ?>
        <div class="actions" style="margin-top:12px;">
            <button class="btn" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save Groups</button>
        </div>
    <?php endif; ?>
</form>

<?php bells_end_page(); ?>
