<?php
function siptrunks_dialplan_h($value) { return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8'); }

function siptrunks_dialplan_columns($pdo, $table) {
    try {
        $stmt = $pdo->query("SHOW COLUMNS FROM `$table`");
        $columns = [];
        foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
            $columns[] = $row['Field'];
        }
        return $columns;
    } catch (Throwable $exc) {
        return [];
    }
}

function siptrunks_dialplan_fetch_groups($pdo) {
    try {
        $stmt = $pdo->query("SELECT `id`, `name` FROM `groups` ORDER BY CAST(`id` AS UNSIGNED), `id`");
        $groups = $stmt->fetchAll(PDO::FETCH_ASSOC);
        array_unshift($groups, ['id' => '0', 'name' => 'All Recipients']);
        return $groups;
    } catch (Throwable $exc) {
        return [['id' => '0', 'name' => 'All Recipients']];
    }
}

function siptrunks_dialplan_ensure_schema($pdo) {
    $pdo->exec("CREATE TABLE IF NOT EXISTS `endpoints-input-siptrunk` (`id` INT NOT NULL AUTO_INCREMENT, `name` VARCHAR(255) NOT NULL DEFAULT '', `extension` VARCHAR(100) NOT NULL DEFAULT '', `group` VARCHAR(255) DEFAULT NULL, `trigger` VARCHAR(100) NOT NULL DEFAULT 'page', `passcode` VARCHAR(64) DEFAULT NULL, PRIMARY KEY (`id`), KEY `extension_idx` (`extension`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci");
    $columns = siptrunks_dialplan_columns($pdo, 'endpoints-input-siptrunk');
    $known = array_flip($columns);
    if (!isset($known['id'])) {
        $pdo->exec("ALTER TABLE `endpoints-input-siptrunk` ADD `id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST");
    }
    if (!isset($known['name'])) {
        $pdo->exec("ALTER TABLE `endpoints-input-siptrunk` ADD `name` VARCHAR(255) NOT NULL DEFAULT ''");
    }
    if (!isset($known['extension'])) {
        $pdo->exec("ALTER TABLE `endpoints-input-siptrunk` ADD `extension` VARCHAR(100) NOT NULL DEFAULT ''");
    }
    if (!isset($known['group'])) {
        $pdo->exec("ALTER TABLE `endpoints-input-siptrunk` ADD `group` VARCHAR(255) DEFAULT NULL");
    }
    if (!isset($known['trigger'])) {
        $pdo->exec("ALTER TABLE `endpoints-input-siptrunk` ADD `trigger` VARCHAR(100) NOT NULL DEFAULT 'page'");
    }
    if (!isset($known['passcode'])) {
        $pdo->exec("ALTER TABLE `endpoints-input-siptrunk` ADD `passcode` VARCHAR(64) DEFAULT NULL");
    }
}

function siptrunks_dialplan_fetch_messages($pdo) {
    try {
        $columns = siptrunks_dialplan_columns($pdo, 'messages');
        $idColumn = in_array('messageid', $columns, true) ? 'messageid' : (in_array('id', $columns, true) ? 'id' : null);
        if (!$idColumn) {
            return [];
        }
        $nameColumn = in_array('name', $columns, true) ? 'name' : $idColumn;
        $stmt = $pdo->query("SELECT `$idColumn` AS id, `$nameColumn` AS name FROM `messages` ORDER BY CAST(`$idColumn` AS UNSIGNED), `$idColumn`");
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    } catch (Throwable $exc) {
        return [];
    }
}

function siptrunks_dialplan_clean_groups($value) {
    $parts = preg_split('/[.,\s]+/', (string)$value);
    $clean = [];
    foreach ($parts as $part) {
        $part = trim($part);
        if ($part !== '' && !in_array($part, $clean, true)) {
            $clean[] = $part;
        }
    }
    return implode('.', $clean);
}

function siptrunks_dialplan_trigger($triggerType, $messageId) {
    if ($triggerType === 'message') {
        return 'message:' . trim((string)$messageId);
    }
    if (in_array($triggerType, ['page', '#testtone', '#echotest'], true)) {
        return $triggerType;
    }
    return 'page';
}

$message = '';
$error = '';
$groups = siptrunks_dialplan_fetch_groups($pdo);
$messages = siptrunks_dialplan_fetch_messages($pdo);
$values = [
    'name' => '',
    'extension' => '',
    'group' => '',
    'trigger_type' => 'page',
    'message_id' => '',
    'require_passcode' => '',
    'passcode' => '',
];

try {
    siptrunks_dialplan_ensure_schema($pdo);
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        foreach ($values as $key => $_default) {
            $values[$key] = trim((string)($_POST[$key] ?? $_default));
        }
        $values['group'] = siptrunks_dialplan_clean_groups($values['group']);
        $trigger = siptrunks_dialplan_trigger($values['trigger_type'], $values['message_id']);
        $passcode = $values['require_passcode'] === '1' ? strtoupper($values['passcode']) : '';
        if (!in_array($values['trigger_type'], ['page', 'message'], true)) {
            $values['group'] = '';
        }

        if ($values['name'] === '' || $values['extension'] === '') {
            throw new RuntimeException('Name and extension are required.');
        }
        if (!preg_match('/^[0-9*#]+$/', $values['extension'])) {
            throw new RuntimeException('Extension can only contain 0-9, *, and #.');
        }
        if (!in_array($values['trigger_type'], ['page', 'message', '#testtone', '#echotest'], true)) {
            throw new RuntimeException('Choose a valid trigger.');
        }
        if ($values['trigger_type'] === 'message' && $values['message_id'] === '') {
            throw new RuntimeException('Choose a message.');
        }
        if (in_array($values['trigger_type'], ['page', 'message'], true) && $values['group'] === '') {
            throw new RuntimeException('Choose at least one group.');
        }
        if ($passcode !== '' && !preg_match('/^[0-9A-D]+$/', $passcode)) {
            throw new RuntimeException('Passcode can only contain 0-9 and A-D.');
        }

        $stmt = $pdo->prepare("SELECT COUNT(*) FROM `endpoints-input-siptrunk` WHERE `extension` = :extension");
        $stmt->execute(['extension' => $values['extension']]);
        if ((int)$stmt->fetchColumn() > 0) {
            throw new RuntimeException('That SIP extension already exists.');
        }

        $stmt = $pdo->prepare("INSERT INTO `endpoints-input-siptrunk` (`name`, `extension`, `group`, `trigger`, `passcode`) VALUES (:name, :extension, :group, :trigger, :passcode)");
        $stmt->execute([
            'name' => $values['name'],
            'extension' => $values['extension'],
            'group' => $values['group'] !== '' ? $values['group'] : null,
            'trigger' => $trigger,
            'passcode' => $passcode !== '' ? $passcode : null,
        ]);
        $message = 'SIP dialplan extension added.';
        $values = ['name' => '', 'extension' => '', 'group' => '', 'trigger_type' => 'page', 'message_id' => '', 'require_passcode' => '', 'passcode' => ''];
    }
} catch (Throwable $exc) {
    $error = $exc->getMessage();
}
?>
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{font-family:Tahoma,sans-serif;margin:0;padding:20px;color:#202124;background:#fff}.form-surface{max-width:720px;background:#fff;border:1px solid #e6e8eb;border-radius:8px;padding:18px;box-shadow:0 1px 3px rgba(0,0,0,.08)}.grid{display:grid;gap:14px}.row{display:grid;gap:6px}label{font-weight:500}.control{padding:10px 11px;border:1px solid #ccd1d5;border-radius:6px;font:inherit;box-sizing:border-box;width:100%;background:#fff;color:#202124}.short-control{max-width:180px}.button{background:#1976D2;color:#fff;border:0;border-radius:6px;padding:10px 14px;font:inherit;cursor:pointer;justify-self:start}.success{background:#E8F5E9;border:1px solid #A5D6A7;color:#1B5E20;padding:10px;border-radius:6px;margin-bottom:12px}.error{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}.dropdown-checklist{position:relative}.dropdown-checklist summary{list-style:none;cursor:pointer;padding:10px 11px;border:1px solid #ccd1d5;border-radius:6px;background:#fff}.dropdown-checklist summary::-webkit-details-marker{display:none}.dropdown-panel{position:absolute;top:calc(100% + 6px);left:0;right:0;z-index:20;border:1px solid #d8dde2;border-radius:6px;padding:8px;display:grid;gap:6px;max-height:220px;overflow:auto;background:#fff;box-shadow:0 8px 18px rgba(0,0,0,.14)}.check{display:flex;gap:8px;align-items:center;font-weight:400}.check.disabled{opacity:.55}.switch-row{display:flex;align-items:center;gap:10px}.switch{position:relative;width:44px;height:24px}.switch input{opacity:0;width:0;height:0}.slider{position:absolute;cursor:pointer;inset:0;background:#9aa0a6;border-radius:999px;transition:.2s}.slider:before{content:"";position:absolute;height:18px;width:18px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s;box-shadow:0 1px 2px rgba(0,0,0,.25)}.switch input:checked + .slider{background:#1976D2}.switch input:checked + .slider:before{transform:translateX(20px)}.hint{color:#5f6368;font-size:.9em}@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.form-surface{background:#232323;border-color:#333;box-shadow:none}.control,.dropdown-checklist summary,.dropdown-panel{background:#171717;border-color:#3a3a3a;color:#eee}.button{background:#BB86FC;color:#000}.hint{color:#aaa}.switch input:checked + .slider{background:#BB86FC}}
</style></head><body>
<?php if ($message): ?><div class="success"><?= siptrunks_dialplan_h($message) ?></div><?php endif; ?><?php if ($error): ?><div class="error"><?= siptrunks_dialplan_h($error) ?></div><?php endif; ?>
<form method="post" class="grid form-surface" id="dialplanForm">
    <div class="row"><label>Name</label><input class="control" name="name" value="<?= siptrunks_dialplan_h($values['name']) ?>" required></div>
    <div class="row"><label>Extension</label><input class="control short-control" name="extension" id="extension" value="<?= siptrunks_dialplan_h($values['extension']) ?>" required pattern="[0-9*#]*" inputmode="tel"></div>
    <div class="row">
        <label>Trigger</label>
        <select class="control" name="trigger_type" id="triggerType">
            <option value="page" <?= $values['trigger_type'] === 'page' ? 'selected' : '' ?>>Paging</option>
            <option value="message" <?= $values['trigger_type'] === 'message' ? 'selected' : '' ?>>Send Message</option>
            <option value="#testtone" <?= $values['trigger_type'] === '#testtone' ? 'selected' : '' ?>>Milliwatt Test Tone</option>
            <option value="#echotest" <?= $values['trigger_type'] === '#echotest' ? 'selected' : '' ?>>Echo Test</option>
        </select>
    </div>
    <div class="row trigger-extra" id="messageRow">
        <label>Message</label>
        <select class="control" name="message_id">
            <option value="">Choose a message</option>
            <?php foreach ($messages as $msg): $mid = (string)($msg['id'] ?? ''); ?>
                <option value="<?= siptrunks_dialplan_h($mid) ?>" <?= $values['message_id'] === $mid ? 'selected' : '' ?>><?= siptrunks_dialplan_h($mid) ?> - <?= siptrunks_dialplan_h($msg['name'] ?? '') ?></option>
            <?php endforeach; ?>
        </select>
    </div>
    <div class="row trigger-extra" id="groupRow">
        <label>Groups</label>
        <input type="hidden" name="group" id="groupValue" value="<?= siptrunks_dialplan_h($values['group']) ?>">
        <details class="dropdown-checklist" id="groupDropdown">
            <summary id="groupSummary">Select groups</summary>
            <div class="dropdown-panel">
                <?php $selectedGroups = array_filter(explode('.', $values['group'])); foreach ($groups as $group): $gid = (string)($group['id'] ?? ''); ?>
                    <label class="check"><input type="checkbox" class="group-check" value="<?= siptrunks_dialplan_h($gid) ?>" data-label="<?= siptrunks_dialplan_h(($gid === '0') ? 'All Recipients' : (($group['name'] ?? '') !== '' ? $group['name'] : $gid)) ?>" <?= in_array($gid, $selectedGroups, true) ? 'checked' : '' ?>> <?= siptrunks_dialplan_h($gid === '0' ? 'All Recipients' : $gid) ?><?php if ($gid !== '0' && ($group['name'] ?? '') !== ''): ?> - <?= siptrunks_dialplan_h($group['name']) ?><?php endif; ?></label>
                <?php endforeach; ?>
                <?php if (!$groups): ?><span class="hint">No groups configured.</span><?php endif; ?>
            </div>
        </details>
    </div>
    <label class="switch-row"><span>Use a passcode</span><span class="switch"><input type="checkbox" name="require_passcode" value="1" id="requirePasscode" <?= $values['require_passcode'] === '1' ? 'checked' : '' ?>><span class="slider"></span></span></label>
    <div class="row" id="passcodeRow">
        <label>Passcode</label>
        <input class="control short-control" name="passcode" id="passcode" value="<?= siptrunks_dialplan_h($values['passcode']) ?>" pattern="[0-9A-D]*" inputmode="text">
    </div>
    <button class="button" type="submit">Add SIP Dialplan Extension</button>
</form>
<script>
const triggerType = document.getElementById('triggerType');
const groupRow = document.getElementById('groupRow');
const messageRow = document.getElementById('messageRow');
const requirePasscode = document.getElementById('requirePasscode');
const passcodeRow = document.getElementById('passcodeRow');
const passcode = document.getElementById('passcode');
const extension = document.getElementById('extension');
const groupValue = document.getElementById('groupValue');
const groupChecks = Array.from(document.querySelectorAll('.group-check'));
const groupSummary = document.getElementById('groupSummary');
function syncTrigger() {
  const value = triggerType.value;
  groupRow.style.display = (value === 'page' || value === 'message') ? 'grid' : 'none';
  messageRow.style.display = value === 'message' ? 'grid' : 'none';
}
function syncPasscode() {
  passcodeRow.style.display = requirePasscode.checked ? 'grid' : 'none';
  if (!requirePasscode.checked) passcode.value = '';
}
function syncGroupsFromChecks() {
  const selectedInputs = groupChecks.filter(input => input.checked);
  const selected = selectedInputs.map(input => input.value);
  groupValue.value = selected.join('.');
  groupSummary.textContent = selectedInputs.length ? selectedInputs.map(input => input.dataset.label || input.value).join(', ') : 'Select groups';
}
function syncAllRecipients() {
  const all = groupChecks.find(input => input.value === '0');
  if (!all) {
    syncGroupsFromChecks();
    return;
  }
  if (all.checked) {
    groupChecks.forEach(input => {
      if (input !== all) {
        input.checked = false;
        input.disabled = true;
        input.closest('.check')?.classList.add('disabled');
      }
    });
  } else {
    groupChecks.forEach(input => {
      input.disabled = false;
      input.closest('.check')?.classList.remove('disabled');
    });
  }
  syncGroupsFromChecks();
}
function blockInvalidInput(input, pattern) {
  input.addEventListener('beforeinput', event => {
    if (event.data && !pattern.test(event.data)) event.preventDefault();
  });
}
triggerType.addEventListener('change', syncTrigger);
requirePasscode.addEventListener('change', syncPasscode);
passcode.addEventListener('input', () => { passcode.value = passcode.value.toUpperCase().replace(/[^0-9A-D]/g, ''); });
extension.addEventListener('input', () => { extension.value = extension.value.replace(/[^0-9*#]/g, ''); });
blockInvalidInput(extension, /^[0-9*#]+$/);
blockInvalidInput(passcode, /^[0-9A-Da-d]+$/);
groupChecks.forEach(input => input.addEventListener('change', syncAllRecipients));
document.getElementById('dialplanForm').addEventListener('submit', syncGroupsFromChecks);
syncTrigger();
syncPasscode();
syncAllRecipients();
</script>
</body></html>
