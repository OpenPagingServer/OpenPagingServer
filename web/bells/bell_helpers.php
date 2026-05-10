<?php
require_once __DIR__ . '/../includes/sidebar-brand.php';

function bells_require_user($pdo) {
    if (session_status() !== PHP_SESSION_ACTIVE) {
        session_start();
    }
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

    return [
        'role' => $userRole,
        'is_admin' => ($userRole === 'admin' || $userRole === 'tempadmin'),
    ];
}

function bells_settings($pdo) {
    $stmt = $pdo->query("SELECT parameter, value FROM systemsettings");
    $settings = [];
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $settings[$row['parameter']] = $row['value'];
    }
    return [
        'product_name' => $settings['product_name'] ?? 'Open Paging Server',
        'favicon' => $settings['favicon'] ?? '',
        'show_online_docs' => $settings['show_online_docs'] ?? '1',
        'use_logo_in_sidebar' => $settings['use_logo_in_sidebar'] ?? '1',
        'sidebar_logo_light' => $settings['sidebar_logo_light'] ?? '/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png',
        'sidebar_logo_dark' => $settings['sidebar_logo_dark'] ?? '/assets/OPENPAGINGSERVER-768x576-DARKMODE.png',
    ];
}

function bells_ensure_schema($pdo) {
    $pdo->exec("
        CREATE TABLE IF NOT EXISTS bell_schedules (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            enabled TINYINT(1) NOT NULL DEFAULT 1,
            timezone VARCHAR(64) NOT NULL DEFAULT 'server',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    ");
    try {
        $pdo->exec("ALTER TABLE bell_schedules ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'server'");
    } catch (Throwable $e) {
    }
    $pdo->exec("
        CREATE TABLE IF NOT EXISTS bell_lists (
            id INT AUTO_INCREMENT PRIMARY KEY,
            schedule_id INT NOT NULL DEFAULT 0,
            name VARCHAR(100) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX schedule_id_idx (schedule_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    ");
    try {
        $pdo->exec("ALTER TABLE bell_lists MODIFY schedule_id INT NOT NULL DEFAULT 0");
    } catch (Throwable $e) {
    }
    $pdo->exec("
        CREATE TABLE IF NOT EXISTS bell_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            list_id INT NOT NULL,
            fire_time TIME NOT NULL,
            audio TEXT NOT NULL,
            days_of_week VARCHAR(32) NOT NULL DEFAULT '0,1,2,3,4,5,6',
            INDEX list_id_idx (list_id),
            INDEX fire_time_idx (fire_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    ");
    try {
        $pdo->exec("ALTER TABLE bell_events ADD COLUMN days_of_week VARCHAR(32) NOT NULL DEFAULT '0,1,2,3,4,5,6'");
    } catch (Throwable $e) {
    }
    $pdo->exec("
        CREATE TABLE IF NOT EXISTS bell_schedule_groups (
            schedule_id INT NOT NULL,
            group_id VARCHAR(100) NOT NULL,
            PRIMARY KEY (schedule_id, group_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    ");
    $pdo->exec("
        CREATE TABLE IF NOT EXISTS bell_calendar (
            schedule_id INT NOT NULL,
            bell_date DATE NOT NULL,
            list_id INT DEFAULT NULL,
            PRIMARY KEY (schedule_id, bell_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    ");
    $pdo->exec("
        CREATE TABLE IF NOT EXISTS bell_calendar_lists (
            schedule_id INT NOT NULL,
            bell_date DATE NOT NULL,
            list_id INT NOT NULL,
            PRIMARY KEY (schedule_id, bell_date, list_id),
            INDEX bell_date_idx (bell_date),
            INDEX list_id_idx (list_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    ");
    $pdo->exec("
        INSERT IGNORE INTO bell_calendar_lists (schedule_id, bell_date, list_id)
        SELECT schedule_id, bell_date, list_id
        FROM bell_calendar
        WHERE list_id IS NOT NULL AND list_id > 0
    ");

    $count = (int)$pdo->query("SELECT COUNT(*) FROM bell_schedules")->fetchColumn();
    if ($count === 0) {
        $pdo->exec("INSERT INTO bell_schedules (name, enabled) VALUES ('Default Bell Schedule', 1)");
        $pdo->exec("INSERT INTO bell_lists (schedule_id, name) VALUES (0, 'Regular Day')");
    }
    $globalListCount = (int)$pdo->query("SELECT COUNT(*) FROM bell_lists WHERE schedule_id = 0")->fetchColumn();
    if ($globalListCount === 0) {
        $pdo->exec("INSERT INTO bell_lists (schedule_id, name) VALUES (0, 'Regular Day')");
    }
}

function bells_schedule($pdo, $scheduleId) {
    $stmt = $pdo->prepare("SELECT id, name, enabled, timezone FROM bell_schedules WHERE id = :id LIMIT 1");
    $stmt->execute(['id' => $scheduleId]);
    $schedule = $stmt->fetch(PDO::FETCH_ASSOC);
    if (!$schedule) {
        http_response_code(404);
        echo "Schedule not found";
        exit;
    }
    return $schedule;
}

function bells_server_timezone_id() {
    $timezoneFile = '/etc/timezone';
    if (is_readable($timezoneFile)) {
        $timezone = trim((string)file_get_contents($timezoneFile));
        if ($timezone !== '' && in_array($timezone, DateTimeZone::listIdentifiers(), true)) {
            return $timezone;
        }
    }
    $localtime = '/etc/localtime';
    if (is_link($localtime)) {
        $target = realpath($localtime);
        $marker = '/zoneinfo/';
        if (is_string($target) && strpos($target, $marker) !== false) {
            $timezone = substr($target, strpos($target, $marker) + strlen($marker));
            if ($timezone !== '' && in_array($timezone, DateTimeZone::listIdentifiers(), true)) {
                return $timezone;
            }
        }
    }
    $timezone = date_default_timezone_get();
    return $timezone ?: 'UTC';
}

function bells_timezone_id($scheduleTimezone = 'server') {
    $scheduleTimezone = trim((string)$scheduleTimezone);
    if ($scheduleTimezone === '' || $scheduleTimezone === 'server') {
        return bells_server_timezone_id();
    }
    if (in_array($scheduleTimezone, DateTimeZone::listIdentifiers(), true)) {
        return $scheduleTimezone;
    }
    return bells_server_timezone_id();
}

function bells_uses_12_hour_clock() {
    $localeOutput = @shell_exec('locale -k LC_TIME 2>/dev/null');
    if (is_string($localeOutput) && preg_match('/^t_fmt="([^"]+)"/m', $localeOutput, $matches)) {
        return strpos($matches[1], '%I') !== false || strpos($matches[1], '%r') !== false;
    }
    return false;
}

function bells_format_time($time) {
    $time = trim((string)$time);
    if ($time === '') {
        return '';
    }
    $dt = DateTime::createFromFormat('H:i:s', strlen($time) === 5 ? $time . ':00' : $time);
    if (!$dt) {
        return $time;
    }
    return $dt->format(bells_uses_12_hour_clock() ? 'g:i:s A' : 'H:i:s');
}

function bells_weekday_names() {
    return [
        '0' => 'Sun',
        '1' => 'Mon',
        '2' => 'Tue',
        '3' => 'Wed',
        '4' => 'Thu',
        '5' => 'Fri',
        '6' => 'Sat',
    ];
}

function bells_normalize_days($days) {
    if (!is_array($days)) {
        $days = [];
    }
    $allowed = array_keys(bells_weekday_names());
    $selected = array_values(array_intersect($allowed, array_unique(array_map('strval', $days))));
    if (empty($selected)) {
        $selected = $allowed;
    }
    return implode(',', $selected);
}

function bells_days_label($value) {
    $names = bells_weekday_names();
    $days = array_values(array_intersect(array_keys($names), preg_split('/\s*,\s*/', (string)$value, -1, PREG_SPLIT_NO_EMPTY)));
    if (count($days) === 7) {
        return 'Every day';
    }
    return implode(', ', array_map(function($day) use ($names) {
        return $names[$day];
    }, $days));
}

function bells_timezone_options($selected = 'server') {
    $selected = $selected ?: 'server';
    $server = bells_server_timezone_id();
    $html = '<option value="server"' . ($selected === 'server' ? ' selected' : '') . '>Server default (' . htmlspecialchars($server) . ')</option>';
    $html .= '<option value="UTC"' . ($selected === 'UTC' ? ' selected' : '') . '>UTC</option>';
    foreach (DateTimeZone::listIdentifiers() as $timezone) {
        if ($timezone === 'UTC') {
            continue;
        }
        $html .= '<option value="' . htmlspecialchars($timezone) . '"' . ($selected === $timezone ? ' selected' : '') . '>' . htmlspecialchars($timezone) . '</option>';
    }
    return $html;
}

function bells_audio_files() {
    $availableAudioFiles = [];
    $audioDir = '/var/lib/openpagingserver/assets';
    if (is_dir($audioDir)) {
        foreach (scandir($audioDir) ?: [] as $file) {
            if (preg_match('/\.(wav|mp3|ogg)$/i', $file)) {
                $availableAudioFiles[] = $file;
            }
        }
    }
    natcasesort($availableAudioFiles);
    return array_values($availableAudioFiles);
}

function bells_available_lists($pdo, $scheduleId = null) {
    if ($scheduleId === null) {
        $stmt = $pdo->query("SELECT id, schedule_id, name FROM bell_lists WHERE schedule_id = 0 ORDER BY name ASC");
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    }
    $stmt = $pdo->prepare("
        SELECT id, schedule_id, name
        FROM bell_lists
        WHERE schedule_id = 0 OR schedule_id = :schedule_id
        ORDER BY schedule_id ASC, name ASC
    ");
    $stmt->execute(['schedule_id' => (int)$scheduleId]);
    return $stmt->fetchAll(PDO::FETCH_ASSOC);
}

function bells_list_scope_label($list, $schedule = null) {
    if ((int)($list['schedule_id'] ?? 0) === 0) {
        return 'System';
    }
    return $schedule ? 'Custom' : 'Schedule';
}

function bells_render_list_options($lists, $schedule = null, $includeAll = false) {
    if ($includeAll) {
        echo '<option value="0">All assigned lists</option>';
    }
    foreach ($lists as $list) {
        $label = bells_list_scope_label($list, $schedule) . ': ' . $list['name'];
        echo '<option value="' . htmlspecialchars((string)$list['id']) . '">' . htmlspecialchars($label) . '</option>';
    }
}

function bells_scope_has_list($pdo, $listId, $scopeScheduleId) {
    $stmt = $pdo->prepare("SELECT COUNT(*) FROM bell_lists WHERE id = :id AND schedule_id = :schedule_id");
    $stmt->execute(['id' => $listId, 'schedule_id' => $scopeScheduleId]);
    return (int)$stmt->fetchColumn() > 0;
}

function bells_schedule_can_use_list($pdo, $listId, $scheduleId) {
    $stmt = $pdo->prepare("SELECT COUNT(*) FROM bell_lists WHERE id = :id AND (schedule_id = 0 OR schedule_id = :schedule_id)");
    $stmt->execute(['id' => $listId, 'schedule_id' => $scheduleId]);
    return (int)$stmt->fetchColumn() > 0;
}

function bells_handle_list_editor_post($pdo, $scopeScheduleId, $redirectPath, $redirectParams = []) {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        return;
    }
    $action = $_POST['action'] ?? '';
    if ($action === 'add_list') {
        $name = trim($_POST['list_name'] ?? '');
        if ($name !== '') {
            $stmt = $pdo->prepare("INSERT INTO bell_lists (schedule_id, name) VALUES (:schedule_id, :name)");
            $stmt->execute(['schedule_id' => $scopeScheduleId, 'name' => $name]);
        }
    } elseif ($action === 'update_list') {
        $listId = (int)($_POST['list_id'] ?? 0);
        $name = trim($_POST['list_name'] ?? '');
        if ($listId > 0 && $name !== '') {
            $stmt = $pdo->prepare("UPDATE bell_lists SET name = :name WHERE id = :id AND schedule_id = :schedule_id");
            $stmt->execute(['name' => $name, 'id' => $listId, 'schedule_id' => $scopeScheduleId]);
        }
    } elseif ($action === 'delete_list') {
        $listId = (int)($_POST['list_id'] ?? 0);
        if ($listId > 0 && bells_scope_has_list($pdo, $listId, $scopeScheduleId)) {
            $stmt = $pdo->prepare("DELETE FROM bell_events WHERE list_id = :list_id");
            $stmt->execute(['list_id' => $listId]);
            $stmt = $pdo->prepare("DELETE FROM bell_calendar WHERE list_id = :list_id");
            $stmt->execute(['list_id' => $listId]);
            $stmt = $pdo->prepare("DELETE FROM bell_calendar_lists WHERE list_id = :list_id");
            $stmt->execute(['list_id' => $listId]);
            $stmt = $pdo->prepare("DELETE FROM bell_lists WHERE id = :id AND schedule_id = :schedule_id");
            $stmt->execute(['id' => $listId, 'schedule_id' => $scopeScheduleId]);
        }
    } elseif ($action === 'add_event' || $action === 'update_event') {
        $listId = (int)($_POST['list_id'] ?? 0);
        $eventId = (int)($_POST['event_id'] ?? 0);
        $time = trim($_POST['fire_time'] ?? '');
        $audioFiles = $_POST['audio_files'] ?? [];
        if (!is_array($audioFiles)) {
            $audioFiles = [];
        }
        $audioFiles = array_values(array_filter(array_map('trim', $audioFiles)));
        $audio = implode(':', $audioFiles);
        $daysOfWeek = bells_normalize_days($_POST['days_of_week'] ?? []);
        if ($listId > 0 && bells_scope_has_list($pdo, $listId, $scopeScheduleId) && preg_match('/^\d{2}:\d{2}(:\d{2})?$/', $time) && $audio !== '') {
            if (strlen($time) === 5) {
                $time .= ':00';
            }
            if ($action === 'add_event') {
                $stmt = $pdo->prepare("INSERT INTO bell_events (list_id, fire_time, audio, days_of_week) VALUES (:list_id, :fire_time, :audio, :days_of_week)");
                $stmt->execute(['list_id' => $listId, 'fire_time' => $time, 'audio' => $audio, 'days_of_week' => $daysOfWeek]);
            } elseif ($eventId > 0) {
                $stmt = $pdo->prepare("
                    UPDATE bell_events e
                    JOIN bell_lists l ON l.id = e.list_id
                    SET e.list_id = :list_id, e.fire_time = :fire_time, e.audio = :audio, e.days_of_week = :days_of_week
                    WHERE e.id = :event_id AND l.schedule_id = :schedule_id
                ");
                $stmt->execute([
                    'list_id' => $listId,
                    'fire_time' => $time,
                    'audio' => $audio,
                    'days_of_week' => $daysOfWeek,
                    'event_id' => $eventId,
                    'schedule_id' => $scopeScheduleId,
                ]);
            }
        }
    } elseif ($action === 'delete_event') {
        $eventId = (int)($_POST['event_id'] ?? 0);
        if ($eventId > 0) {
            $stmt = $pdo->prepare("
                DELETE e FROM bell_events e
                JOIN bell_lists l ON l.id = e.list_id
                WHERE e.id = :id AND l.schedule_id = :schedule_id
            ");
            $stmt->execute(['id' => $eventId, 'schedule_id' => $scopeScheduleId]);
        }
    }
    bells_redirect($redirectPath, $redirectParams);
}

function bells_hidden_fields($fields) {
    foreach ($fields as $name => $value) {
        echo '<input type="hidden" name="' . htmlspecialchars((string)$name) . '" value="' . htmlspecialchars((string)$value) . '">';
    }
}

function bells_render_list_editor($pdo, $scopeScheduleId, $postPath, $hiddenFields = [], $schedule = null) {
    $availableAudioFiles = bells_audio_files();
    $stmt = $pdo->prepare("SELECT id, schedule_id, name FROM bell_lists WHERE schedule_id = :schedule_id ORDER BY name ASC");
    $stmt->execute(['schedule_id' => $scopeScheduleId]);
    $lists = $stmt->fetchAll(PDO::FETCH_ASSOC);

    $eventsByList = [];
    if (!empty($lists)) {
        $listIds = array_column($lists, 'id');
        $placeholders = implode(',', array_fill(0, count($listIds), '?'));
        $stmt = $pdo->prepare("SELECT id, list_id, fire_time, audio, days_of_week FROM bell_events WHERE list_id IN ($placeholders) ORDER BY fire_time ASC, id ASC");
        $stmt->execute($listIds);
        foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $event) {
            $eventsByList[(int)$event['list_id']][] = $event;
        }
    }
?>
<form class="card compact-form" method="POST" action="<?= htmlspecialchars($postPath) ?>">
    <input type="hidden" name="action" value="add_list">
    <?php bells_hidden_fields($hiddenFields); ?>
    <div class="row">
        <input name="list_name" placeholder="New list name" required>
        <button class="btn" type="submit"><i class="fa-solid fa-plus"></i> Add List</button>
    </div>
</form>

<?php if (empty($lists)): ?>
    <div class="info-card"><p class="muted">No bell lists yet.</p></div>
<?php endif; ?>

<?php foreach ($lists as $list): ?>
    <div class="card bell-list-card">
        <div class="list-editor-head">
            <div>
                <div class="list-title"><?= htmlspecialchars($list['name']) ?></div>
                <div class="list-meta"><?= htmlspecialchars(bells_list_scope_label($list, $schedule)) ?> bell list</div>
            </div>
            <div class="actions">
                <button class="btn icon secondary" type="button" title="Edit List" onclick="openBellListModal(this)" data-list-id="<?= htmlspecialchars((string)$list['id']) ?>" data-list-name="<?= htmlspecialchars($list['name']) ?>"><i class="fa-solid fa-pen-to-square"></i></button>
                <button class="btn" type="button" onclick="openBellEventModal(this)" data-mode="add" data-list-id="<?= htmlspecialchars((string)$list['id']) ?>" data-list-name="<?= htmlspecialchars($list['name']) ?>"><i class="fa-solid fa-plus"></i> Add Bell</button>
                <form method="POST" action="<?= htmlspecialchars($postPath) ?>" onsubmit="return confirm('Delete this bell list?')">
                    <input type="hidden" name="action" value="delete_list">
                    <?php bells_hidden_fields($hiddenFields); ?>
                    <input type="hidden" name="list_id" value="<?= htmlspecialchars((string)$list['id']) ?>">
                    <button class="btn icon danger" type="submit" title="Delete List"><i class="fa-solid fa-trash"></i></button>
                </form>
            </div>
        </div>

        <?php foreach (($eventsByList[(int)$list['id']] ?? []) as $event): ?>
            <div class="event">
                <div class="event-main">
                    <strong><?= htmlspecialchars(bells_format_time($event['fire_time'])) ?></strong>
                    <span class="muted"><?= htmlspecialchars(bells_days_label($event['days_of_week'] ?? '0,1,2,3,4,5,6')) ?></span>
                    <div class="muted"><?= htmlspecialchars($event['audio']) ?></div>
                </div>
                <div class="actions">
                    <button
                        class="btn icon secondary"
                        type="button"
                        title="Edit Bell"
                        onclick="openBellEventModal(this)"
                        data-mode="edit"
                        data-list-id="<?= htmlspecialchars((string)$list['id']) ?>"
                        data-list-name="<?= htmlspecialchars($list['name']) ?>"
                        data-event-id="<?= htmlspecialchars((string)$event['id']) ?>"
                        data-fire-time="<?= htmlspecialchars($event['fire_time']) ?>"
                        data-days="<?= htmlspecialchars($event['days_of_week'] ?? '0,1,2,3,4,5,6') ?>"
                        data-audio="<?= htmlspecialchars($event['audio']) ?>"
                    ><i class="fa-solid fa-pen-to-square"></i></button>
                    <form method="POST" action="<?= htmlspecialchars($postPath) ?>" onsubmit="return confirm('Delete this bell?')">
                        <input type="hidden" name="action" value="delete_event">
                        <?php bells_hidden_fields($hiddenFields); ?>
                        <input type="hidden" name="event_id" value="<?= htmlspecialchars((string)$event['id']) ?>">
                        <button class="btn icon danger" type="submit" title="Delete Bell"><i class="fa-solid fa-trash"></i></button>
                    </form>
                </div>
            </div>
        <?php endforeach; ?>
    </div>
<?php endforeach; ?>

<div class="modal-backdrop" id="bellListBackdrop" onclick="closeBellListModal(event)">
    <form class="modal-card" method="POST" action="<?= htmlspecialchars($postPath) ?>" onclick="event.stopPropagation()">
        <input type="hidden" name="action" value="update_list">
        <?php bells_hidden_fields($hiddenFields); ?>
        <input type="hidden" name="list_id" id="editListId">
        <div class="modal-head">
            <h2>Edit Bell List</h2>
            <button class="btn icon secondary" type="button" onclick="hideBellListModal()" title="Close"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="field">
            <label for="editListName">List name</label>
            <input id="editListName" name="list_name" required>
        </div>
        <div class="actions modal-actions">
            <button class="btn secondary" type="button" onclick="hideBellListModal()">Cancel</button>
            <button class="btn" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save</button>
        </div>
    </form>
</div>

<div class="modal-backdrop" id="bellEventBackdrop" onclick="closeBellEventModal(event)">
    <form class="modal-card modal-wide bell-audio-form" id="bellEventForm" method="POST" action="<?= htmlspecialchars($postPath) ?>" onclick="event.stopPropagation()">
        <?php bells_hidden_fields($hiddenFields); ?>
        <input type="hidden" name="action" id="eventAction" value="add_event">
        <input type="hidden" name="list_id" id="eventListId">
        <input type="hidden" name="event_id" id="eventId">
        <div id="eventAudioHidden"></div>
        <div class="modal-head">
            <div>
                <h2 id="eventModalTitle">Add Bell</h2>
                <div class="muted" id="eventModalSubtitle"></div>
            </div>
            <button class="btn icon secondary" type="button" onclick="hideBellEventModal()" title="Close"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="row">
            <div class="field">
                <label for="eventFireTime">Bell time</label>
                <input id="eventFireTime" class="bell-time-input" type="time" name="fire_time" step="1" required>
            </div>
        </div>
        <div class="field">
            <label>Active days</label>
            <div class="weekday-row">
                <?php foreach (bells_weekday_names() as $dayValue => $dayName): ?>
                    <label class="weekday-chip">
                        <input class="event-day" type="checkbox" name="days_of_week[]" value="<?= htmlspecialchars($dayValue) ?>">
                        <span><?= htmlspecialchars($dayName) ?></span>
                    </label>
                <?php endforeach; ?>
            </div>
        </div>
        <div class="field">
            <label>Audio files</label>
            <?php if (empty($availableAudioFiles)): ?>
                <p class="muted">No audio files found in /var/lib/openpagingserver/assets.</p>
            <?php else: ?>
                <div class="transfer-list-container modal-transfer">
                    <div class="tl-panel">
                        <div class="tl-header">Available Files</div>
                        <input type="text" class="tl-search" id="eventAudioSearch" placeholder="Search files..." oninput="renderEventAudioLists()">
                        <div class="tl-list available-audio-list" id="eventAvailableAudio"></div>
                    </div>
                    <div class="tl-controls">
                        <button type="button" class="btn icon" onclick="moveEventAudioRight()" title="Move Selected Right"><i class="fa-solid fa-angle-right"></i></button>
                        <button type="button" class="btn icon" onclick="moveEventAudioLeft()" title="Move Selected Left"><i class="fa-solid fa-angle-left"></i></button>
                        <button type="button" class="btn icon" onclick="moveEventAudioUp()" title="Move Selected Up"><i class="fa-solid fa-angle-up"></i></button>
                        <button type="button" class="btn icon" onclick="moveEventAudioDown()" title="Move Selected Down"><i class="fa-solid fa-angle-down"></i></button>
                    </div>
                    <div class="tl-panel">
                        <div class="tl-header">Selected Files (In Order)</div>
                        <div class="tl-list selected-audio-list" id="eventSelectedAudio"></div>
                    </div>
                </div>
            <?php endif; ?>
        </div>
        <div class="actions modal-actions">
            <button class="btn secondary" type="button" onclick="hideBellEventModal()">Cancel</button>
            <button class="btn" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save Bell</button>
        </div>
    </form>
</div>

<script>
const bellAudioFiles = <?= json_encode($availableAudioFiles) ?>;
let eventSelectedAudio = [];
let highlightedAvailableAudio = null;
let highlightedSelectedAudio = null;

document.querySelectorAll('.bell-time-input').forEach(input => {
  input.addEventListener('change', () => {
    if (/^\d{2}:\d{2}$/.test(input.value)) {
      input.value = input.value + ':00';
    }
  });
});

function showModal(id) {
  document.getElementById(id).classList.add('open');
  document.body.classList.add('modal-open');
}

function hideModal(id) {
  document.getElementById(id).classList.remove('open');
  document.body.classList.remove('modal-open');
}

function openBellListModal(button) {
  document.getElementById('editListId').value = button.dataset.listId || '';
  document.getElementById('editListName').value = button.dataset.listName || '';
  showModal('bellListBackdrop');
  setTimeout(() => document.getElementById('editListName').focus(), 0);
}

function closeBellListModal(event) {
  if (event.target.id === 'bellListBackdrop') hideBellListModal();
}

function hideBellListModal() {
  hideModal('bellListBackdrop');
}

function openBellEventModal(button) {
  const mode = button.dataset.mode || 'add';
  document.getElementById('eventAction').value = mode === 'edit' ? 'update_event' : 'add_event';
  document.getElementById('eventId').value = button.dataset.eventId || '';
  document.getElementById('eventListId').value = button.dataset.listId || '';
  document.getElementById('eventModalTitle').textContent = mode === 'edit' ? 'Edit Bell' : 'Add Bell';
  document.getElementById('eventModalSubtitle').textContent = button.dataset.listName || '';
  document.getElementById('eventFireTime').value = button.dataset.fireTime || '';
  const selectedDays = (button.dataset.days || '0,1,2,3,4,5,6').split(',');
  document.querySelectorAll('.event-day').forEach(input => {
    input.checked = selectedDays.includes(input.value);
  });
  eventSelectedAudio = (button.dataset.audio || '').split(':').map(item => item.trim()).filter(Boolean);
  highlightedAvailableAudio = null;
  highlightedSelectedAudio = null;
  const search = document.getElementById('eventAudioSearch');
  if (search) search.value = '';
  renderEventAudioLists();
  showModal('bellEventBackdrop');
  setTimeout(() => document.getElementById('eventFireTime').focus(), 0);
}

function closeBellEventModal(event) {
  if (event.target.id === 'bellEventBackdrop') hideBellEventModal();
}

function hideBellEventModal() {
  hideModal('bellEventBackdrop');
}

function audioItem(text, selected, onClick) {
  const item = document.createElement('div');
  item.className = 'tl-item' + (selected ? ' selected' : '');
  item.textContent = text;
  item.onclick = onClick;
  return item;
}

function renderEventAudioLists() {
  const available = document.getElementById('eventAvailableAudio');
  const selected = document.getElementById('eventSelectedAudio');
  const hidden = document.getElementById('eventAudioHidden');
  if (!available || !selected || !hidden) return;
  const search = (document.getElementById('eventAudioSearch')?.value || '').toLowerCase();
  available.innerHTML = '';
  selected.innerHTML = '';
  hidden.innerHTML = '';
  bellAudioFiles
    .filter(file => !eventSelectedAudio.includes(file))
    .filter(file => file.toLowerCase().includes(search))
    .forEach(file => available.appendChild(audioItem(file, highlightedAvailableAudio === file, () => {
      highlightedAvailableAudio = highlightedAvailableAudio === file ? null : file;
      highlightedSelectedAudio = null;
      renderEventAudioLists();
    })));
  eventSelectedAudio.forEach(file => {
    selected.appendChild(audioItem(file, highlightedSelectedAudio === file, () => {
      highlightedSelectedAudio = highlightedSelectedAudio === file ? null : file;
      highlightedAvailableAudio = null;
      renderEventAudioLists();
    }));
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'audio_files[]';
    input.value = file;
    hidden.appendChild(input);
  });
}

function moveEventAudioRight() {
  if (!highlightedAvailableAudio) return;
  eventSelectedAudio.push(highlightedAvailableAudio);
  highlightedAvailableAudio = null;
  renderEventAudioLists();
}

function moveEventAudioLeft() {
  if (!highlightedSelectedAudio) return;
  eventSelectedAudio = eventSelectedAudio.filter(file => file !== highlightedSelectedAudio);
  highlightedSelectedAudio = null;
  renderEventAudioLists();
}

function moveEventAudioUp() {
  if (!highlightedSelectedAudio) return;
  const index = eventSelectedAudio.indexOf(highlightedSelectedAudio);
  if (index > 0) {
    [eventSelectedAudio[index - 1], eventSelectedAudio[index]] = [eventSelectedAudio[index], eventSelectedAudio[index - 1]];
    renderEventAudioLists();
  }
}

function moveEventAudioDown() {
  if (!highlightedSelectedAudio) return;
  const index = eventSelectedAudio.indexOf(highlightedSelectedAudio);
  if (index >= 0 && index < eventSelectedAudio.length - 1) {
    [eventSelectedAudio[index + 1], eventSelectedAudio[index]] = [eventSelectedAudio[index], eventSelectedAudio[index + 1]];
    renderEventAudioLists();
  }
}

document.getElementById('bellEventForm').addEventListener('submit', event => {
  if (eventSelectedAudio.length === 0) {
    event.preventDefault();
  }
});

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  hideBellListModal();
  hideBellEventModal();
});
</script>
<?php
}

function bells_schedule_settings_card($schedule, $activeTab = 'settings') {
    $scheduleId = (int)$schedule['id'];
    $nav = [
        'calendar' => ['Calendar', '/bells/calendar.php?schedule_id=' . urlencode((string)$scheduleId), 'fa-calendar-days'],
        'bells' => ['Bells', '/bells/lists.php?schedule_id=' . urlencode((string)$scheduleId), 'fa-bell'],
        'groups' => ['Groups', '/bells/groups.php?schedule_id=' . urlencode((string)$scheduleId), 'fa-user-group'],
    ];
?>
<form class="card schedule-settings-card" method="POST" action="/bells/edit.php">
    <input type="hidden" name="id" value="<?= htmlspecialchars((string)$scheduleId) ?>">
    <input type="hidden" name="action" value="save">
    <input type="hidden" name="return_to" value="<?= htmlspecialchars($_SERVER['REQUEST_URI'] ?? '/bells/edit.php?id=' . $scheduleId) ?>">
    <div class="schedule-settings-grid">
        <div class="field">
            <label for="schedule_name">Schedule name</label>
            <input id="schedule_name" name="name" value="<?= htmlspecialchars($schedule['name']) ?>" required>
        </div>
        <div class="field">
            <label for="schedule_timezone">Time zone</label>
            <select id="schedule_timezone" name="timezone">
                <?= bells_timezone_options($schedule['timezone'] ?? 'server') ?>
            </select>
        </div>
        <label class="checkbox-row schedule-enabled">
            <input type="checkbox" name="enabled" <?= (int)$schedule['enabled'] === 1 ? 'checked' : '' ?>>
            <span>Enabled</span>
        </label>
        <button class="btn" type="submit"><i class="fa-solid fa-floppy-disk"></i> Save Schedule</button>
    </div>
    <div class="schedule-tabs">
        <?php foreach ($nav as $key => $item): ?>
            <a class="<?= $activeTab === $key ? 'active' : '' ?>" href="<?= htmlspecialchars($item[1]) ?>">
                <i class="fa-solid <?= htmlspecialchars($item[2]) ?>"></i> <?= htmlspecialchars($item[0]) ?>
            </a>
        <?php endforeach; ?>
    </div>
</form>
<?php
}

function bells_redirect($path, $params = []) {
    $query = $params ? '?' . http_build_query($params) : '';
    header("Location: " . $path . $query);
    exit;
}

function bells_endpoint_manager_request($command) {
    $socket = @fsockopen('127.0.0.1', 50000, $errno, $errstr, 2);
    if (!$socket) {
        return [null, "Endpoint manager is not reachable: $errstr"];
    }
    stream_set_timeout($socket, 5);
    fwrite($socket, $command . "\n");
    $response = '';
    while (!feof($socket)) {
        $response .= fgets($socket, 65536);
        if (substr($response, -1) === "\n") {
            break;
        }
    }
    fclose($socket);
    $decoded = json_decode($response, true);
    if (!is_array($decoded)) {
        return [null, "Endpoint manager returned an invalid response."];
    }
    return [$decoded, null];
}

function bells_output_capable($endpoint) {
    if (array_key_exists('output_capable', $endpoint) && !$endpoint['output_capable']) {
        return false;
    }
    $value = strtolower(trim(($endpoint['direction'] ?? '') . ' ' . ($endpoint['input_type'] ?? '')));
    if (strpos($value, 'output') !== false) {
        return true;
    }
    $capabilities = $endpoint['capabilities'] ?? [];
    return is_array($capabilities) && (in_array('output', $capabilities, true) || in_array('bells', $capabilities, true));
}

function bells_begin_page($settings, $user, $title) {
    $product_name = $settings['product_name'];
    $favicon = $settings['favicon'];
    $show_online_docs = $settings['show_online_docs'];
    $isAdmin = $user['is_admin'];
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title><?= htmlspecialchars($title) ?> - <?= htmlspecialchars($product_name) ?></title>
<?php if (!empty($favicon)): ?>
<link rel="icon" href="<?= htmlspecialchars($favicon) ?>" type="image/x-icon">
<?php endif; ?>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet" />
<link href="/assets/sidebar-brand.css" rel="stylesheet" />
<style>
body,html{margin:0;padding:0;font-family:"Tahoma",sans-serif;font-weight:300;background:#FFF;height:100%;}
#sidebar{width:220px;background:#1976D2;color:#FFF;height:100vh;position:fixed;top:0;left:0;display:flex;flex-direction:column;box-shadow:2px 0 8px rgba(0,0,0,.2);transition:transform .3s ease;z-index:1200;}
@media(max-width:767px){#sidebar{transform:translateX(-100%);}#sidebar.open{transform:translateX(0);}}
#sidebar h2{text-align:center;padding:20px 0;margin:0;font-weight:500;background:#1565C0;font-size:1.2em;color:#FFF;}
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{color:#FFF;padding:12px 20px;display:block;border-bottom:1px solid rgba(255,255,255,.1);text-decoration:none;transition:background .3s;font-size:.9em;text-align:left;box-sizing:border-box;}
#sidebar a i,.logout-btn i,.logout-btn-mobile i,.admin-only i{margin-right:8px;width:20px;}
#sidebar a:hover,#sidebar a.active{background:#1565C0;}
.logout-btn{background:#C62828;border:none;cursor:pointer;margin-top:auto;transition:background-color .3s;}
.logout-btn-mobile{background:#C62828;border:none;cursor:pointer;transition:background-color .3s;display:none;}
@media(max-width:767px){.logout-btn{display:none;}.logout-btn-mobile{display:block;}}
#mobile-header{display:flex;background:#1565C0;color:#FFF;padding:calc(12px + env(safe-area-inset-top)) 16px 12px;align-items:center;justify-content:space-between;position:fixed;top:0;left:0;right:0;z-index:1100;}
#mobile-header h2{margin:0;font-size:1.1em;font-weight:400;color:#FFF;}
#mobile-header .hamburger{font-size:1.5em;cursor:pointer;}
#overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.3);z-index:900;}
#overlay.active{display:block;}
#content{margin-left:220px;padding:24px;min-height:100vh;width:calc(100% - 220px);box-sizing:border-box;}
@media(max-width:767px){#content{margin-left:0;width:100%;padding-top:70px;}}
@media(min-width:768px){#mobile-header{display:none;}}
h1{font-weight:400;margin:0;}
.header-actions{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;gap:16px;flex-wrap:wrap;}
.header-main{display:flex;flex-direction:column;gap:5px;}
.server-clock{text-align:right;color:#444;font-size:.96em;line-height:1.4;min-width:220px;}
.server-clock strong{display:block;font-size:1.08em;color:#202124;}
.btn{background:#1976D2;color:#FFF;border:none;border-radius:4px;padding:10px 12px;cursor:pointer;font:inherit;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:8px;white-space:nowrap;min-height:38px;box-sizing:border-box;}
.btn.icon{width:38px;height:38px;padding:0;flex:0 0 auto;}
.btn.secondary{background:transparent;color:#1976D2;}
.btn.danger{background:#C62828;}
.actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:16px;}
.summary-item{border:1px solid #EEE;border-radius:8px;padding:12px;background:#FFF;}
.summary-item strong{display:block;font-size:1.4em;font-weight:500;}
.info-card,.card{background:#FFF;border:1px solid #EEE;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.1);padding:16px;margin-bottom:16px;}
.info-card.flush{padding:0;overflow:hidden;}
.list{list-style:none;margin:0;padding:0;}
.list-item{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 18px;border-bottom:1px solid #EEE;background:#FFF;}
.list-item:last-child{border-bottom:none;}
.list-main{min-width:0;}
.list-title{font-size:1.05em;font-weight:500;color:#202124;overflow-wrap:anywhere;}
.list-meta{color:#555;margin-top:4px;font-size:.92em;}
.field{display:flex;flex-direction:column;gap:6px;margin-bottom:14px;}
.field label{font-size:.9em;color:#555;font-weight:500;}
input,select{border:1px solid #CCC;border-radius:4px;padding:10px;font:inherit;background:#FFF;color:#202124;box-sizing:border-box;width:100%;}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;}
.row>*{flex:1;}
.checkbox-row{display:flex;align-items:center;gap:8px;padding:7px 0;}
.checkbox-row input{width:auto;}
.muted{color:#777;font-size:.9em;}
.error{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:12px;border-radius:8px;margin-bottom:16px;}
.calendar-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;}
.calendar-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;}
.dow{text-align:center;color:#666;font-size:.82em;font-weight:500;padding:4px;}
.day{border:1px solid #EEE;border-radius:6px;min-height:126px;padding:7px;background:#FFF;display:flex;flex-direction:column;gap:6px;}
.day.empty{background:transparent;border-color:transparent;}
.day-number{font-weight:500;color:#333;display:flex;align-items:center;justify-content:space-between;gap:6px;}
.day-list{display:flex;align-items:center;justify-content:space-between;gap:6px;border:1px solid #EEE;border-radius:4px;padding:5px 6px;font-size:.86em;background:#FAFAFA;}
.day-add{display:none;margin-top:2px;}
.day.adding .day-add{display:block;}
.day-add .row{gap:5px;}
.bulk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;align-items:end;}
.weekday-row{display:flex;flex-wrap:wrap;gap:6px;}
.weekday-chip{display:inline-flex;align-items:center;gap:5px;border:1px solid #DDD;border-radius:4px;padding:6px 8px;background:#FAFAFA;font-size:.9em;}
.event{display:flex;align-items:center;justify-content:space-between;gap:10px;border-bottom:1px solid #EEE;padding:10px 0;}
.event:last-child{border-bottom:none;}
.event-main{min-width:0;overflow-wrap:anywhere;}
.bell-editor{border:1px solid #EEE;border-radius:8px;padding:12px;background:#FAFAFA;margin-top:12px;}
.bell-editor summary{cursor:pointer;font-weight:500;color:#1976D2;display:flex;align-items:center;gap:8px;}
.bell-editor summary::-webkit-details-marker{display:none;}
.bell-editor summary:before{content:'+';display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;background:#1976D2;color:#FFF;font-weight:700;}
.bell-editor[open] summary:before{content:'-';}
.bell-list-card{padding:0;overflow:hidden;}
.bell-list-card>.list-editor-head{padding:16px 18px;border-bottom:1px solid #EEE;}
.list-editor-head{display:flex;align-items:center;justify-content:space-between;gap:16px;}
.compact-form{padding:14px;}
.modal-open{overflow:hidden;}
.modal-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.58);z-index:2000;align-items:center;justify-content:center;padding:18px;box-sizing:border-box;}
.modal-backdrop.open{display:flex;}
.modal-card{background:#FFF;border-radius:8px;box-shadow:0 18px 50px rgba(0,0,0,.35);width:min(520px,100%);max-height:calc(100vh - 36px);overflow:auto;padding:18px;box-sizing:border-box;}
.modal-card.modal-wide{width:min(880px,100%);}
.modal-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:14px;}
.modal-head h2{font-size:1.25em;font-weight:500;margin:0;color:#202124;}
.modal-actions{justify-content:flex-end;margin-top:14px;}
.modal-transfer .tl-panel{min-height:220px;}
.modal-transfer .tl-list{max-height:260px;}
.transfer-list-container{display:flex;gap:10px;align-items:stretch;margin-top:8px;}
.tl-panel{flex:1;min-width:0;border:1px solid #DDD;border-radius:6px;overflow:hidden;display:flex;flex-direction:column;min-height:160px;background:#FFF;}
.tl-header{background:#F5F5F5;padding:8px 10px;font-weight:500;border-bottom:1px solid #DDD;font-size:.9em;}
.tl-search{border:none;border-bottom:1px solid #DDD;border-radius:0;padding:10px;width:100%;box-sizing:border-box;outline:none;}
.tl-list{flex:1;overflow-y:auto;padding:5px;min-height:100px;max-height:180px;}
.tl-item{padding:8px 10px;margin-bottom:4px;background:#FAFAFA;border:1px solid #EEE;cursor:pointer;user-select:none;border-radius:3px;font-size:.95em;overflow-wrap:anywhere;}
.tl-item:hover{background:#F0F0F0;}
.tl-item.selected{background:#1976D2;color:#FFF;border-color:#1565C0;}
.tl-item.dragging{opacity:.5;}
.tl-controls{display:flex;flex-direction:column;justify-content:center;gap:10px;flex:0 0 auto;}
.schedule-settings-card{padding:14px 16px;}
.schedule-settings-grid{display:grid;grid-template-columns:minmax(220px,1fr) minmax(220px,1fr) auto auto;gap:12px;align-items:end;}
.schedule-settings-grid .field{margin-bottom:0;}
.schedule-enabled{padding:10px 0;margin:0;align-self:end;white-space:nowrap;}
.schedule-tabs{display:flex;gap:8px;flex-wrap:wrap;border-top:1px solid #EEE;margin-top:14px;padding-top:12px;}
.schedule-tabs a{color:#1976D2;text-decoration:none;padding:8px 10px;border-radius:4px;display:inline-flex;align-items:center;gap:7px;font-size:.94em;}
.schedule-tabs a.active,.schedule-tabs a:hover{background:#E3F2FD;color:#1565C0;}
@media(max-width:980px){.schedule-settings-grid{grid-template-columns:1fr 1fr;}.schedule-enabled{align-self:center;}}
@media(max-width:620px){.schedule-settings-grid{grid-template-columns:1fr;}.server-clock{text-align:left;}}
@media(max-width:850px){.transfer-list-container{flex-direction:column;}.tl-controls{flex-direction:row;justify-content:flex-start;}}
@media(prefers-color-scheme:dark){
body,html{background:#121212;color:#E0E0E0;}
#sidebar,#mobile-header{background:#424242;}
#sidebar h2{background:#303030;color:#FFF;}
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{color:#E0E0E0;}
#sidebar a.active,#sidebar a:hover{background:#505050;}
#content{background:#121212;}
.server-clock,.field label,.muted,.dow{color:#BBB;}
.server-clock strong,.list-title,.day-number{color:#EDEDED;}
.info-card,.card,.summary-item,.list-item,.day{border-color:#333;background:#1E1E1E;}
.list-item,.event{border-bottom-color:#333;}
.list-meta{color:#CCC;}
input,select{background:#121212;border-color:#444;color:#E0E0E0;}
.btn{background:#BB86FC;color:#000;}
.btn.secondary{background:transparent;color:#BB86FC;}
.btn.danger{background:#CF6679;color:#000;}
.error{background:#3B1515;border-color:#6D2A2A;color:#FFCDD2;}
.tl-panel{background:#1E1E1E;border-color:#444;}
.tl-header{background:#2A2A2A;border-bottom-color:#444;}
.tl-search{background:#222;border-bottom-color:#444;color:#FFF;}
.tl-item{background:#2A2A2A;border-color:#333;color:#E0E0E0;}
.tl-item:hover{background:#333;}
.tl-item.selected{background:#BB86FC;color:#000;border-color:#A370F7;}
.bell-editor{background:#1A1A1A;border-color:#333;}
.bell-editor summary{color:#BB86FC;}
.bell-editor summary:before{background:#BB86FC;color:#000;}
.bell-list-card>.list-editor-head{border-bottom-color:#333;}
.modal-backdrop{background:rgba(0,0,0,.72);}
.modal-card{background:#1E1E1E;color:#E0E0E0;}
.modal-head h2{color:#EDEDED;}
.day-list{background:#242424;border-color:#333;}
.weekday-chip{background:#242424;border-color:#444;}
.schedule-tabs{border-top-color:#333;}
.schedule-tabs a{color:#BB86FC;}
.schedule-tabs a.active,.schedule-tabs a:hover{background:#2A2433;color:#D9B8FF;}
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
    <a href="/bells" class="active"><i class="fa-solid fa-bell"></i> Bells</a>
    <a href="/assets/"><i class="fa-solid fa-folder-open"></i> Assets</a>
    <?php if ($isAdmin): ?>
      <a href="/admin/manage-users.php" class="admin-only"><i class="fa-solid fa-users-cog"></i> Manage Users</a>
      <a href="/admin/manage-endpoints.php" class="admin-only"><i class="fa-solid fa-shapes"></i> Manage Endpoints</a>
      <a href="/admin/manage-groups.php" class="admin-only"><i class="fa-solid fa-user-group"></i> Manage Groups</a>
      <a href="/admin/settings/general.php" class="admin-only"><i class="fa-solid fa-cogs"></i> Server Settings</a>
    <?php endif; ?>
    <?php if ($show_online_docs == '1'): ?>
    <a href="https://docs.openpagingserver.org"><i class="fa-solid fa-book"></i> Online Documentation</a>
    <?php endif; ?>
    <button class="logout-btn-mobile" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
    <button class="logout-btn" onclick="logout()"><i class="fa-solid fa-sign-out-alt"></i> Logout</button>
</div>
<div id="content" onclick="closeSidebarOnContentClick()">
<?php
}

function bells_page_header($title, $subtitle = '', $actions = '') {
?>
<div class="header-actions">
    <div class="header-main">
        <h1><?= htmlspecialchars($title) ?></h1>
        <?php if ($subtitle !== ''): ?><div class="muted"><?= htmlspecialchars($subtitle) ?></div><?php endif; ?>
    </div>
    <div class="server-clock">
        <span id="systemDate">Loading server date...</span>
        <strong id="systemTime">Loading server time...</strong>
    </div>
    <?php if ($actions !== ''): ?><div class="actions"><?= $actions ?></div><?php endif; ?>
</div>
<?php
}

function bells_end_page() {
?>
</div>
<script>
let bellsClock = {
  timestamp: null,
  uses12Hour: false,
  timezone: undefined
};

function formatBellClockDate(date) {
  return date.toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    timeZone: bellsClock.timezone
  });
}

function formatBellClockTime(date) {
  return date.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: bellsClock.uses12Hour,
    timeZone: bellsClock.timezone
  });
}

function renderBellClock() {
  if (bellsClock.timestamp === null) return;
  const now = new Date(bellsClock.timestamp);
  document.getElementById('systemDate').textContent = formatBellClockDate(now);
  document.getElementById('systemTime').textContent = formatBellClockTime(now);
  bellsClock.timestamp += 1000;
}

async function refreshServerClock(){
  try {
    const response = await fetch('/bells/time.php', { cache: 'no-store' });
    const data = await response.json();
    bellsClock.timestamp = data.timestamp_ms;
    bellsClock.uses12Hour = !!data.uses_12_hour;
    bellsClock.timezone = data.timezone || undefined;
    renderBellClock();
  } catch (error) {
    document.getElementById('systemDate').textContent = 'Server date unavailable';
    document.getElementById('systemTime').textContent = 'Server time unavailable';
  }
}
refreshServerClock();
setInterval(renderBellClock, 1000);
setInterval(refreshServerClock, 10000);
function toggleSidebar(){const sidebar=document.getElementById("sidebar");sidebar.classList.toggle("open");document.getElementById("overlay").classList.toggle("active",sidebar.classList.contains("open"));}
function closeSidebar(){document.getElementById("sidebar").classList.remove("open");document.getElementById("overlay").classList.remove("active");}
function closeSidebarOnContentClick(){if(document.getElementById("sidebar").classList.contains("open"))closeSidebar();}
function logout(){window.location.href="/logout.php";}
</script>
</body>
</html>
<?php
}
