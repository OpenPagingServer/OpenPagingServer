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

$stmt = $pdo->query("SELECT id, name, enabled FROM bell_schedules ORDER BY name ASC");
$schedules = $stmt->fetchAll(PDO::FETCH_ASSOC);
$enabledCount = 0;
foreach ($schedules as $schedule) {
    if ((int)$schedule['enabled'] === 1) {
        $enabledCount++;
    }
}
$listCount = (int)$pdo->query("SELECT COUNT(*) FROM bell_lists WHERE schedule_id = 0")->fetchColumn();

bells_begin_page($settings, $user, 'Bells');
bells_page_header(
    'Bells',
    'Schedule overview',
    '<a class="btn secondary" href="/bells/bell-lists.php"><i class="fa-solid fa-list-check"></i> Bell Lists</a><a class="btn" href="/bells/new.php"><i class="fa-solid fa-plus"></i> New Schedule</a>'
);
?>

<div class="summary-grid">
    <div class="summary-item">
        <strong><?= htmlspecialchars((string)count($schedules)) ?></strong>
        <span class="muted">Schedules</span>
    </div>
    <div class="summary-item">
        <strong><?= htmlspecialchars((string)$enabledCount) ?></strong>
        <span class="muted">Enabled</span>
    </div>
    <a class="summary-item" href="/bells/bell-lists.php" style="text-decoration:none;color:inherit;">
        <strong><?= htmlspecialchars((string)$listCount) ?></strong>
        <span class="muted">System Bell Lists</span>
    </a>
</div>

<div class="info-card flush">
    <?php if (empty($schedules)): ?>
        <p class="muted" style="text-align:center;padding:20px;">No bell schedules found.</p>
    <?php else: ?>
        <ul class="list">
            <?php foreach ($schedules as $schedule): ?>
                <li class="list-item">
                    <div class="list-main">
                        <div class="list-title"><?= htmlspecialchars($schedule['name']) ?></div>
                        <div class="list-meta">
                            <?= (int)$schedule['enabled'] === 1 ? 'Enabled' : 'Disabled' ?>
                        </div>
                    </div>
                    <div class="actions">
                        <a class="btn icon secondary" href="/bells/edit.php?id=<?= urlencode($schedule['id']) ?>" title="Edit Schedule"><i class="fa-solid fa-pen-to-square"></i></a>
                    </div>
                </li>
            <?php endforeach; ?>
        </ul>
    <?php endif; ?>
</div>

<?php bells_end_page(); ?>
