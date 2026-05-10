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
$month = $_GET['month'] ?? $_POST['month'] ?? date('Y-m');
if (!preg_match('/^\d{4}-\d{2}$/', $month)) {
    $month = date('Y-m');
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = $_POST['action'] ?? '';
    if ($action === 'add_day_list') {
        $date = trim($_POST['bell_date'] ?? '');
        $listId = (int)($_POST['list_id'] ?? 0);
        if ($listId > 0 && bells_schedule_can_use_list($pdo, $listId, $scheduleId) && preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) {
            $stmt = $pdo->prepare("INSERT IGNORE INTO bell_calendar_lists (schedule_id, bell_date, list_id) VALUES (:schedule_id, :bell_date, :list_id)");
            $stmt->execute(['schedule_id' => $scheduleId, 'bell_date' => $date, 'list_id' => $listId]);
        }
    } elseif ($action === 'remove_day_list') {
        $date = trim($_POST['bell_date'] ?? '');
        $listId = (int)($_POST['list_id'] ?? 0);
        if ($listId > 0 && preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) {
            $stmt = $pdo->prepare("DELETE FROM bell_calendar_lists WHERE schedule_id = :schedule_id AND bell_date = :bell_date AND list_id = :list_id");
            $stmt->execute(['schedule_id' => $scheduleId, 'bell_date' => $date, 'list_id' => $listId]);
        }
    } elseif ($action === 'bulk_add' || $action === 'bulk_delete' || $action === 'bulk_override') {
        $startDate = trim($_POST['start_date'] ?? '');
        $endDate = trim($_POST['end_date'] ?? '');
        $days = $_POST['days_of_week'] ?? [];
        if (!is_array($days)) {
            $days = [];
        }
        $days = array_unique(array_map('strval', $days));
        $listId = (int)($_POST['list_id'] ?? 0);
        $targetListId = (int)($_POST['target_list_id'] ?? 0);
        if (preg_match('/^\d{4}-\d{2}-\d{2}$/', $startDate) && preg_match('/^\d{4}-\d{2}-\d{2}$/', $endDate) && !empty($days)) {
            $startDt = new DateTime($startDate);
            $endDt = new DateTime($endDate);
            if ($endDt < $startDt) {
                [$startDt, $endDt] = [$endDt, $startDt];
            }
            $insertStmt = $pdo->prepare("INSERT IGNORE INTO bell_calendar_lists (schedule_id, bell_date, list_id) VALUES (:schedule_id, :bell_date, :list_id)");
            $deleteOneStmt = $pdo->prepare("DELETE FROM bell_calendar_lists WHERE schedule_id = :schedule_id AND bell_date = :bell_date AND list_id = :list_id");
            $deleteAllStmt = $pdo->prepare("DELETE FROM bell_calendar_lists WHERE schedule_id = :schedule_id AND bell_date = :bell_date");
            for ($day = clone $startDt; $day <= $endDt; $day->modify('+1 day')) {
                if (!in_array($day->format('w'), $days, true)) {
                    continue;
                }
                $date = $day->format('Y-m-d');
                if ($action === 'bulk_add' && $listId > 0 && bells_schedule_can_use_list($pdo, $listId, $scheduleId)) {
                    $insertStmt->execute(['schedule_id' => $scheduleId, 'bell_date' => $date, 'list_id' => $listId]);
                } elseif ($action === 'bulk_delete' && $listId > 0 && bells_schedule_can_use_list($pdo, $listId, $scheduleId)) {
                    $deleteOneStmt->execute(['schedule_id' => $scheduleId, 'bell_date' => $date, 'list_id' => $listId]);
                } elseif ($action === 'bulk_delete') {
                    $deleteAllStmt->execute(['schedule_id' => $scheduleId, 'bell_date' => $date]);
                } elseif ($action === 'bulk_override' && $targetListId > 0 && bells_schedule_can_use_list($pdo, $targetListId, $scheduleId)) {
                    if ($listId > 0) {
                        if (bells_schedule_can_use_list($pdo, $listId, $scheduleId)) {
                            $deleteOneStmt->execute(['schedule_id' => $scheduleId, 'bell_date' => $date, 'list_id' => $listId]);
                        }
                    } else {
                        $deleteAllStmt->execute(['schedule_id' => $scheduleId, 'bell_date' => $date]);
                    }
                    $insertStmt->execute(['schedule_id' => $scheduleId, 'bell_date' => $date, 'list_id' => $targetListId]);
                }
            }
        }
    }
    bells_redirect('/bells/calendar.php', ['schedule_id' => $scheduleId, 'month' => $month]);
}

$lists = bells_available_lists($pdo, $scheduleId);

$monthStart = strtotime($month . '-01');
$daysInMonth = (int)date('t', $monthStart);
$firstWeekday = (int)date('w', $monthStart);
$prevMonth = date('Y-m', strtotime('-1 month', $monthStart));
$nextMonth = date('Y-m', strtotime('+1 month', $monthStart));

$calendarAssignments = [];
$start = $month . '-01';
$end = date('Y-m-t', strtotime($start));
$stmt = $pdo->prepare("
    SELECT c.bell_date, c.list_id, l.name, l.schedule_id
    FROM bell_calendar_lists c
    JOIN bell_lists l ON l.id = c.list_id
    WHERE c.schedule_id = :schedule_id
      AND (l.schedule_id = 0 OR l.schedule_id = :scope_schedule_id)
      AND c.bell_date BETWEEN :start AND :end
    ORDER BY c.bell_date ASC, l.schedule_id ASC, l.name ASC
");
$stmt->execute(['schedule_id' => $scheduleId, 'scope_schedule_id' => $scheduleId, 'start' => $start, 'end' => $end]);
foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
    $calendarAssignments[$row['bell_date']][] = $row;
}

bells_begin_page($settings, $user, 'Bell Calendar');
bells_page_header(
    'Bell Calendar',
    $schedule['name'],
    '<a class="btn secondary" href="/bells/edit.php?id=' . htmlspecialchars((string)$scheduleId) . '"><i class="fa-solid fa-arrow-left"></i> Back</a>'
);
?>

<?php bells_schedule_settings_card($schedule, 'calendar'); ?>

<div class="card">
    <form method="POST" action="/bells/calendar.php" class="bulk-grid">
        <input type="hidden" name="schedule_id" value="<?= htmlspecialchars((string)$scheduleId) ?>">
        <input type="hidden" name="month" value="<?= htmlspecialchars($month) ?>">
        <div class="field">
            <label for="start_date">Start date</label>
            <input id="start_date" type="date" name="start_date" value="<?= htmlspecialchars($start) ?>" required>
        </div>
        <div class="field">
            <label for="end_date">End date</label>
            <input id="end_date" type="date" name="end_date" value="<?= htmlspecialchars($end) ?>" required>
        </div>
        <div class="field">
            <label for="bulk_list_id">List</label>
            <select id="bulk_list_id" name="list_id">
                <?php bells_render_list_options($lists, $schedule, true); ?>
            </select>
        </div>
        <div class="field">
            <label for="target_list_id">Override to</label>
            <select id="target_list_id" name="target_list_id">
                <option value="">Choose target list</option>
                <?php bells_render_list_options($lists, $schedule, false); ?>
            </select>
        </div>
        <div class="field">
            <label>Days</label>
            <div class="weekday-row">
                <?php foreach (bells_weekday_names() as $dayValue => $dayName): ?>
                    <label class="weekday-chip">
                        <input type="checkbox" name="days_of_week[]" value="<?= htmlspecialchars($dayValue) ?>" checked>
                        <span><?= htmlspecialchars($dayName) ?></span>
                    </label>
                <?php endforeach; ?>
            </div>
        </div>
        <div class="actions">
            <button class="btn" type="submit" name="action" value="bulk_add"><i class="fa-solid fa-plus"></i> Add</button>
            <button class="btn danger" type="submit" name="action" value="bulk_delete"><i class="fa-solid fa-trash"></i> Delete</button>
            <button class="btn secondary" type="submit" name="action" value="bulk_override"><i class="fa-solid fa-repeat"></i> Override</button>
        </div>
    </form>
</div>

<div class="card">
    <div class="calendar-head">
        <a class="btn secondary" href="/bells/calendar.php?schedule_id=<?= urlencode((string)$scheduleId) ?>&month=<?= urlencode($prevMonth) ?>"><i class="fa-solid fa-angle-left"></i></a>
        <strong><?= htmlspecialchars(date('F Y', $monthStart)) ?></strong>
        <a class="btn secondary" href="/bells/calendar.php?schedule_id=<?= urlencode((string)$scheduleId) ?>&month=<?= urlencode($nextMonth) ?>"><i class="fa-solid fa-angle-right"></i></a>
    </div>
    <div class="calendar-grid">
        <?php foreach (['Sun','Mon','Tue','Wed','Thu','Fri','Sat'] as $dayName): ?>
            <div class="dow"><?= htmlspecialchars($dayName) ?></div>
        <?php endforeach; ?>
        <?php for ($i = 0; $i < $firstWeekday; $i++): ?><div class="day empty"></div><?php endfor; ?>
        <?php for ($day = 1; $day <= $daysInMonth; $day++): ?>
            <?php $date = sprintf('%s-%02d', $month, $day); ?>
            <div class="day">
                <div class="day-number">
                    <span><?= htmlspecialchars((string)$day) ?></span>
                    <button class="btn icon secondary" type="button" onclick="toggleDayAdd(this)" title="Add List"><i class="fa-solid fa-plus"></i></button>
                </div>
                <?php foreach (($calendarAssignments[$date] ?? []) as $assignedList): ?>
                    <div class="day-list">
                        <span><?= htmlspecialchars(bells_list_scope_label($assignedList, $schedule) . ': ' . $assignedList['name']) ?></span>
                        <form method="POST" action="/bells/calendar.php">
                            <input type="hidden" name="action" value="remove_day_list">
                            <input type="hidden" name="schedule_id" value="<?= htmlspecialchars((string)$scheduleId) ?>">
                            <input type="hidden" name="month" value="<?= htmlspecialchars($month) ?>">
                            <input type="hidden" name="bell_date" value="<?= htmlspecialchars($date) ?>">
                            <input type="hidden" name="list_id" value="<?= htmlspecialchars($assignedList['list_id']) ?>">
                            <button class="btn icon danger" type="submit" title="Remove"><i class="fa-solid fa-xmark"></i></button>
                        </form>
                    </div>
                <?php endforeach; ?>
                <form method="POST" action="/bells/calendar.php" class="day-add">
                    <input type="hidden" name="action" value="add_day_list">
                    <input type="hidden" name="schedule_id" value="<?= htmlspecialchars((string)$scheduleId) ?>">
                    <input type="hidden" name="month" value="<?= htmlspecialchars($month) ?>">
                    <input type="hidden" name="bell_date" value="<?= htmlspecialchars($date) ?>">
                    <div class="row">
                        <select name="list_id" required>
                            <option value="">List</option>
                            <?php bells_render_list_options($lists, $schedule, false); ?>
                        </select>
                        <button class="btn icon" type="submit" title="Add"><i class="fa-solid fa-check"></i></button>
                    </div>
                </form>
            </div>
        <?php endfor; ?>
    </div>
</div>

<script>
function toggleDayAdd(button) {
  button.closest('.day').classList.toggle('adding');
}
</script>

<?php bells_end_page(); ?>
