<?php
function siptrunks_endpoint_h($value) {
    return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8');
}

function siptrunks_clean_groups($value) {
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

function siptrunks_fetch_groups($pdo) {
    try {
        $stmt = $pdo->query("SELECT `id`, `name` FROM `groups` ORDER BY CAST(`id` AS UNSIGNED), `id`");
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    } catch (Throwable $exc) {
        return [];
    }
}

function siptrunks_ensure_dialplan_schema($pdo) {
    $pdo->exec("CREATE TABLE IF NOT EXISTS `endpoints-input-siptrunk` (`id` INT NOT NULL AUTO_INCREMENT, `name` VARCHAR(255) NOT NULL DEFAULT '', `extension` VARCHAR(100) NOT NULL DEFAULT '', `group` VARCHAR(255) DEFAULT NULL, `trigger` VARCHAR(100) NOT NULL DEFAULT 'page', `passcode` VARCHAR(64) DEFAULT NULL, PRIMARY KEY (`id`), KEY `extension_idx` (`extension`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci");
    $columns = siptrunks_table_columns($pdo, 'endpoints-input-siptrunk');
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

function siptrunks_table_columns($pdo, $table) {
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

function siptrunks_fetch_messages($pdo) {
    try {
        $columns = siptrunks_table_columns($pdo, 'messages');
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

function siptrunks_parse_trigger($trigger) {
    $trigger = trim((string)$trigger);
    if (strpos($trigger, 'message:') === 0) {
        return ['message', substr($trigger, 8)];
    }
    if (in_array($trigger, ['page', '#testtone', '#echotest'], true)) {
        return [$trigger, ''];
    }
    return ['page', ''];
}

function siptrunks_build_trigger($triggerType, $messageId) {
    if ($triggerType === 'message') {
        return 'message:' . trim((string)$messageId);
    }
    if (in_array($triggerType, ['page', '#testtone', '#echotest'], true)) {
        return $triggerType;
    }
    return 'page';
}

function siptrunks_render_dialplan_form($row, $message, $error, $groups, $messages) {
    [$triggerType, $messageId] = siptrunks_parse_trigger($row['trigger'] ?? 'page');
    $selectedGroups = array_filter(explode('.', (string)($row['group'] ?? '')));
    $requirePasscode = trim((string)($row['passcode'] ?? '')) !== '';
    ?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:Tahoma,sans-serif;margin:0;padding:20px;color:#202124;background:#fff}.form-surface{max-width:720px;background:#fff;border:1px solid #e6e8eb;border-radius:8px;padding:18px;box-shadow:0 1px 3px rgba(0,0,0,.08)}.grid{display:grid;gap:14px}.row{display:grid;gap:6px}label{font-weight:500}.control{padding:10px 11px;border:1px solid #ccd1d5;border-radius:6px;font:inherit;box-sizing:border-box;width:100%;background:#fff;color:#202124}.short-control{max-width:180px}.button{background:#1976D2;color:#fff;border:0;border-radius:6px;padding:10px 14px;font:inherit;cursor:pointer;justify-self:start}.success{background:#E8F5E9;border:1px solid #A5D6A7;color:#1B5E20;padding:10px;border-radius:6px;margin-bottom:12px}.error{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}.dropdown-checklist{position:relative}.dropdown-checklist summary{list-style:none;cursor:pointer;padding:10px 11px;border:1px solid #ccd1d5;border-radius:6px;background:#fff}.dropdown-checklist summary::-webkit-details-marker{display:none}.dropdown-panel{margin-top:6px;border:1px solid #d8dde2;border-radius:6px;padding:8px;display:grid;gap:6px;max-height:220px;overflow:auto;background:#fff}.check{display:flex;gap:8px;align-items:center;font-weight:400}.switch-row{display:flex;align-items:center;gap:10px}.switch{position:relative;width:44px;height:24px}.switch input{opacity:0;width:0;height:0}.slider{position:absolute;cursor:pointer;inset:0;background:#9aa0a6;border-radius:999px;transition:.2s}.slider:before{content:"";position:absolute;height:18px;width:18px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s;box-shadow:0 1px 2px rgba(0,0,0,.25)}.switch input:checked + .slider{background:#1976D2}.switch input:checked + .slider:before{transform:translateX(20px)}.hint{color:#5f6368;font-size:.9em}@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.form-surface{background:#232323;border-color:#333;box-shadow:none}.control,.dropdown-checklist summary,.dropdown-panel{background:#171717;border-color:#3a3a3a;color:#eee}.button{background:#BB86FC;color:#000}.hint{color:#aaa}.switch input:checked + .slider{background:#BB86FC}}
</style>
</head>
<body>
<?php if ($message): ?><div class="success"><?= siptrunks_endpoint_h($message) ?></div><?php endif; ?>
<?php if ($error): ?><div class="error"><?= siptrunks_endpoint_h($error) ?></div><?php endif; ?>
<?php if ($row): ?>
<form method="post" class="grid form-surface" id="dialplanForm">
    <div class="row"><label>Name</label><input class="control" name="name" value="<?= siptrunks_endpoint_h($row['name'] ?? '') ?>" required></div>
    <div class="row"><label>Extension</label><input class="control short-control" name="extension" id="extension" value="<?= siptrunks_endpoint_h($row['extension'] ?? '') ?>" required pattern="[0-9*#]*" inputmode="tel"></div>
    <div class="row">
        <label>Trigger</label>
        <select class="control" name="trigger_type" id="triggerType">
            <option value="page" <?= $triggerType === 'page' ? 'selected' : '' ?>>Paging</option>
            <option value="message" <?= $triggerType === 'message' ? 'selected' : '' ?>>Send Message</option>
            <option value="#testtone" <?= $triggerType === '#testtone' ? 'selected' : '' ?>>Milliwatt Test Tone</option>
            <option value="#echotest" <?= $triggerType === '#echotest' ? 'selected' : '' ?>>Echo Test</option>
        </select>
    </div>
    <div class="row trigger-extra" id="messageRow">
        <label>Message</label>
        <select class="control" name="message_id">
            <option value="">Choose a message</option>
            <?php foreach ($messages as $msg): $mid = (string)($msg['id'] ?? ''); ?>
                <option value="<?= siptrunks_endpoint_h($mid) ?>" <?= $messageId === $mid ? 'selected' : '' ?>><?= siptrunks_endpoint_h($mid) ?> - <?= siptrunks_endpoint_h($msg['name'] ?? '') ?></option>
            <?php endforeach; ?>
        </select>
    </div>
    <div class="row trigger-extra" id="groupRow">
        <label>Groups</label>
        <input type="hidden" name="group" id="groupValue" value="<?= siptrunks_endpoint_h($row['group'] ?? '') ?>">
        <details class="dropdown-checklist" id="groupDropdown">
            <summary id="groupSummary">Select groups</summary>
            <div class="dropdown-panel">
                <?php foreach ($groups as $group): $gid = (string)($group['id'] ?? ''); ?>
                    <label class="check"><input type="checkbox" class="group-check" value="<?= siptrunks_endpoint_h($gid) ?>" <?= in_array($gid, $selectedGroups, true) ? 'checked' : '' ?>> <?= siptrunks_endpoint_h($gid) ?><?php if (($group['name'] ?? '') !== ''): ?> - <?= siptrunks_endpoint_h($group['name']) ?><?php endif; ?></label>
                <?php endforeach; ?>
                <?php if (!$groups): ?><span class="hint">No groups configured.</span><?php endif; ?>
            </div>
        </details>
    </div>
    <label class="switch-row"><span>Use a passcode</span><span class="switch"><input type="checkbox" name="require_passcode" value="1" id="requirePasscode" <?= $requirePasscode ? 'checked' : '' ?>><span class="slider"></span></span></label>
    <div class="row" id="passcodeRow">
        <label>Passcode</label>
        <input class="control short-control" name="passcode" id="passcode" value="<?= siptrunks_endpoint_h($row['passcode'] ?? '') ?>" pattern="[0-9A-D]*" inputmode="text">
    </div>
    <button class="button" type="submit">Save SIP Dialplan Extension</button>
</form>
<?php endif; ?>
<script>
const triggerType=document.getElementById('triggerType'),groupRow=document.getElementById('groupRow'),messageRow=document.getElementById('messageRow'),requirePasscode=document.getElementById('requirePasscode'),passcodeRow=document.getElementById('passcodeRow'),passcode=document.getElementById('passcode'),extension=document.getElementById('extension'),groupValue=document.getElementById('groupValue'),groupChecks=Array.from(document.querySelectorAll('.group-check')),groupSummary=document.getElementById('groupSummary');
if (triggerType) {
function syncTrigger(){const v=triggerType.value;groupRow.style.display=(v==='page'||v==='message')?'grid':'none';messageRow.style.display=v==='message'?'grid':'none'}
function syncPasscode(){passcodeRow.style.display=requirePasscode.checked?'grid':'none';if(!requirePasscode.checked)passcode.value=''}
function syncGroupsFromChecks(){const selected=groupChecks.filter(i=>i.checked).map(i=>i.value);groupValue.value=selected.join('.');groupSummary.textContent=selected.length?selected.join('.'):'Select groups'}
function blockInvalidInput(input,pattern){input.addEventListener('beforeinput',e=>{if(e.data&&!pattern.test(e.data))e.preventDefault()})}
triggerType.addEventListener('change',syncTrigger);requirePasscode.addEventListener('change',syncPasscode);passcode.addEventListener('input',()=>{passcode.value=passcode.value.toUpperCase().replace(/[^0-9A-D]/g,'')});extension.addEventListener('input',()=>{extension.value=extension.value.replace(/[^0-9*#]/g,'')});blockInvalidInput(extension,/^[0-9*#]+$/);blockInvalidInput(passcode,/^[0-9A-Da-d]+$/);groupChecks.forEach(i=>i.addEventListener('change',syncGroupsFromChecks));document.getElementById('dialplanForm').addEventListener('submit',syncGroupsFromChecks);syncTrigger();syncPasscode();syncGroupsFromChecks();
}
</script>
</body>
</html>
<?php
}

function siptrunks_hold_column($pdo) {
    $candidates = ['holdbehabior', 'hold-behavipr', 'holdbehavior', 'holdbehaviour', 'hold-behavior'];
    try {
        $stmt = $pdo->query("SHOW COLUMNS FROM `sip-trunks`");
        $columns = [];
        foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $column) {
            $columns[$column['Field']] = true;
        }
        foreach ($candidates as $candidate) {
            if (isset($columns[$candidate])) {
                return $candidate;
            }
        }
    } catch (Throwable $exc) {
    }
    return null;
}

$message = '';
$error = '';
$row = null;
$id = 0;

try {
    if (strpos($endpointId, 'dialplan-') === 0) {
        $id = (int)substr($endpointId, strlen('dialplan-'));
        if ($id < 1) {
            throw new RuntimeException('Invalid SIP dialplan extension.');
        }
        siptrunks_ensure_dialplan_schema($pdo);
        if ($_SERVER['REQUEST_METHOD'] === 'POST') {
            $name = trim((string)($_POST['name'] ?? ''));
            $extension = trim((string)($_POST['extension'] ?? ''));
            $group = siptrunks_clean_groups($_POST['group'] ?? '');
            $triggerType = trim((string)($_POST['trigger_type'] ?? 'page'));
            $messageId = trim((string)($_POST['message_id'] ?? ''));
            $passcode = ($_POST['require_passcode'] ?? '') === '1' ? strtoupper(trim((string)($_POST['passcode'] ?? ''))) : '';
            $trigger = siptrunks_build_trigger($triggerType, $messageId);
            if (!in_array($triggerType, ['page', 'message'], true)) {
                $group = '';
            }
            if ($name === '' || $extension === '') {
                throw new RuntimeException('Name and extension are required.');
            }
            if (!preg_match('/^[0-9*#]+$/', $extension)) {
                throw new RuntimeException('Extension can only contain 0-9, *, and #.');
            }
            if ($triggerType === 'message' && $messageId === '') {
                throw new RuntimeException('Choose a message.');
            }
            if (in_array($triggerType, ['page', 'message'], true) && $group === '') {
                throw new RuntimeException('Choose at least one group.');
            }
            if ($passcode !== '' && !preg_match('/^[0-9A-D]+$/', $passcode)) {
                throw new RuntimeException('Passcode can only contain 0-9 and A-D.');
            }
            $stmt = $pdo->prepare("SELECT COUNT(*) FROM `endpoints-input-siptrunk` WHERE `extension` = :extension AND `id` <> :id");
            $stmt->execute(['extension' => $extension, 'id' => $id]);
            if ((int)$stmt->fetchColumn() > 0) {
                throw new RuntimeException('That SIP extension already exists.');
            }
            $stmt = $pdo->prepare("UPDATE `endpoints-input-siptrunk` SET `name` = :name, `extension` = :extension, `group` = :group, `trigger` = :trigger, `passcode` = :passcode WHERE `id` = :id");
            $stmt->execute(['name' => $name, 'extension' => $extension, 'group' => $group !== '' ? $group : null, 'trigger' => $trigger, 'passcode' => $passcode !== '' ? $passcode : null, 'id' => $id]);
            $message = 'SIP dialplan extension updated.';
        }
        $stmt = $pdo->prepare("SELECT `id`, `name`, `extension`, `group`, `trigger`, `passcode` FROM `endpoints-input-siptrunk` WHERE `id` = :id");
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        if (!$row) {
            throw new RuntimeException('SIP dialplan extension not found.');
        }
        siptrunks_render_dialplan_form($row, $message, $error, siptrunks_fetch_groups($pdo), siptrunks_fetch_messages($pdo));
        return;
    }

    if (strpos($endpointId, 'trunk-') !== 0) {
        throw new RuntimeException('Invalid SIP trunk endpoint.');
    }
    $id = (int)substr($endpointId, strlen('trunk-'));
    if ($id < 1) {
        throw new RuntimeException('Invalid SIP trunk endpoint.');
    }
    $pdo->exec("CREATE TABLE IF NOT EXISTS `sip-trunks` (`id` INT NOT NULL AUTO_INCREMENT, `name` VARCHAR(255) NOT NULL DEFAULT '', `auth` ENUM('IP','USERPASS') NOT NULL DEFAULT 'IP', `username` VARCHAR(255) DEFAULT NULL, `password` VARCHAR(255) DEFAULT NULL, `ipaddr` VARCHAR(255) NOT NULL DEFAULT '0.0.0.0', `status` VARCHAR(255) NOT NULL DEFAULT 'Offline', `holdbehavior` ENUM('passrtp','pausertp','endcall') NOT NULL DEFAULT 'passrtp', PRIMARY KEY (`id`), KEY `auth_idx` (`auth`), KEY `username_idx` (`username`), KEY `ipaddr_idx` (`ipaddr`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci");
    $holdColumn = siptrunks_hold_column($pdo);
    if (!$holdColumn) {
        try {
            $pdo->exec("ALTER TABLE `sip-trunks` ADD `holdbehavior` ENUM('passrtp','pausertp','endcall') NOT NULL DEFAULT 'passrtp'");
            $holdColumn = 'holdbehavior';
        } catch (Throwable $exc) {
        }
    }

    $stmt = $pdo->prepare("SELECT `id`, `name`, `auth`, `username`, `password`, `ipaddr`, `status` FROM `sip-trunks` WHERE `id` = :id");
    $stmt->execute(['id' => $id]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if (!$row) {
        throw new RuntimeException('SIP trunk not found.');
    }
    if ($holdColumn) {
        $stmt = $pdo->prepare("SELECT `$holdColumn` FROM `sip-trunks` WHERE `id` = :id");
        $stmt->execute(['id' => $id]);
        $row['holdbehavior'] = $stmt->fetchColumn() ?: 'passrtp';
    } else {
        $row['holdbehavior'] = 'passrtp';
    }

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $authType = strtoupper((string)($row['auth'] ?? 'IP'));
        $name = trim((string)($_POST['name'] ?? ''));
        $ipaddr = trim((string)($_POST['ipaddr'] ?? ''));
        $username = trim((string)($_POST['username'] ?? ''));
        $password = trim((string)($_POST['password'] ?? ''));
        $holdbehavior = strtolower(trim((string)($_POST['holdbehavior'] ?? 'passrtp')));

        if ($name === '') {
            throw new RuntimeException('Name is required.');
        }
        if (!in_array($holdbehavior, ['passrtp', 'pausertp', 'endcall'], true)) {
            $holdbehavior = 'passrtp';
        }
        if ($authType === 'IP') {
            if ($ipaddr === '' || !filter_var($ipaddr, FILTER_VALIDATE_IP)) {
                throw new RuntimeException('Enter a valid IP address.');
            }
            $stmt = $pdo->prepare("SELECT COUNT(*) FROM `sip-trunks` WHERE `auth` = 'IP' AND `ipaddr` = :ipaddr AND `id` <> :id");
            $stmt->execute(['ipaddr' => $ipaddr, 'id' => $id]);
            if ((int)$stmt->fetchColumn() > 0) {
                throw new RuntimeException('That SIP trunk IP already exists.');
            }
            $holdSql = $holdColumn ? ", `$holdColumn` = :holdbehavior" : "";
            $stmt = $pdo->prepare("UPDATE `sip-trunks` SET `name` = :name, `username` = NULL, `password` = NULL, `ipaddr` = :ipaddr$holdSql WHERE `id` = :id");
            $params = ['name' => $name, 'ipaddr' => $ipaddr, 'id' => $id];
            if ($holdColumn) {
                $params['holdbehavior'] = $holdbehavior;
            }
            $stmt->execute($params);
        } else {
            if ($username === '' || $password === '') {
                throw new RuntimeException('Username and password are required.');
            }
            if ($ipaddr === '') {
                $ipaddr = '0.0.0.0';
            }
            if (strpos($ipaddr, '/') === false && !filter_var($ipaddr, FILTER_VALIDATE_IP)) {
                throw new RuntimeException('Enter a valid IP restriction.');
            }
            $stmt = $pdo->prepare("SELECT COUNT(*) FROM `sip-trunks` WHERE `auth` = 'USERPASS' AND `username` = :username AND `id` <> :id");
            $stmt->execute(['username' => $username, 'id' => $id]);
            if ((int)$stmt->fetchColumn() > 0) {
                throw new RuntimeException('That SIP trunk username already exists.');
            }
            $holdSql = $holdColumn ? ", `$holdColumn` = :holdbehavior" : "";
            $stmt = $pdo->prepare("UPDATE `sip-trunks` SET `name` = :name, `username` = :username, `password` = :password, `ipaddr` = :ipaddr$holdSql WHERE `id` = :id");
            $params = ['name' => $name, 'username' => $username, 'password' => $password, 'ipaddr' => $ipaddr, 'id' => $id];
            if ($holdColumn) {
                $params['holdbehavior'] = $holdbehavior;
            }
            $stmt->execute($params);
        }
        $message = 'SIP trunk updated.';
        $stmt = $pdo->prepare("SELECT `id`, `name`, `auth`, `username`, `password`, `ipaddr`, `status` FROM `sip-trunks` WHERE `id` = :id");
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        if ($row) {
            $row['holdbehavior'] = $holdbehavior;
        }
    }
} catch (Throwable $exc) {
    $error = $exc->getMessage();
}

if (strpos($endpointId, 'dialplan-') === 0) {
    if (!$row && $id > 0) {
        try {
            $stmt = $pdo->prepare("SELECT `id`, `name`, `extension`, `group`, `trigger`, `passcode` FROM `endpoints-input-siptrunk` WHERE `id` = :id");
            $stmt->execute(['id' => $id]);
            $row = $stmt->fetch(PDO::FETCH_ASSOC);
        } catch (Throwable $exc) {
        }
    }
    siptrunks_render_dialplan_form($row, $message, $error, siptrunks_fetch_groups($pdo), siptrunks_fetch_messages($pdo));
    return;
}

$authType = strtoupper((string)($row['auth'] ?? 'IP'));
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}.grid{display:grid;gap:12px}.row{display:grid;gap:6px}label{font-weight:500}.control{padding:10px;border:1px solid #ddd;border-radius:4px;font:inherit}.button{background:#1976D2;color:#fff;border:0;border-radius:4px;padding:10px 14px;font:inherit;cursor:pointer}.success{background:#E8F5E9;border:1px solid #A5D6A7;color:#1B5E20;padding:10px;border-radius:6px;margin-bottom:12px}.error{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}.meta{color:#5f6368;margin:0 0 14px}@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.control{background:#171717;border-color:#333;color:#eee}.button{background:#BB86FC;color:#000}.meta{color:#aaa}}
</style>
</head>
<body>
<?php if ($message): ?><div class="success"><?= siptrunks_endpoint_h($message) ?></div><?php endif; ?>
<?php if ($error): ?><div class="error"><?= siptrunks_endpoint_h($error) ?></div><?php endif; ?>
<?php if ($row): ?>
    <p class="meta">Current status: <?= siptrunks_endpoint_h($row['status'] ?? 'Offline') ?></p>
    <form method="post" class="grid">
        <div class="row"><label>Name</label><input class="control" name="name" value="<?= siptrunks_endpoint_h($row['name'] ?? '') ?>" required></div>
        <?php if ($authType === 'IP'): ?>
            <div class="row"><label>IP Address</label><input class="control" name="ipaddr" value="<?= siptrunks_endpoint_h($row['ipaddr'] ?? '') ?>" required></div>
        <?php else: ?>
            <div class="row"><label>Username</label><input class="control" name="username" value="<?= siptrunks_endpoint_h($row['username'] ?? '') ?>" required></div>
            <div class="row"><label>Password</label><input class="control" type="password" name="password" value="<?= siptrunks_endpoint_h($row['password'] ?? '') ?>" required></div>
            <div class="row"><label>IP Restriction</label><input class="control" name="ipaddr" value="<?= siptrunks_endpoint_h($row['ipaddr'] ?? '0.0.0.0') ?>" required></div>
        <?php endif; ?>
        <div class="row">
            <label>Hold Behavior</label>
            <select class="control" name="holdbehavior">
                <?php $holdValue = strtolower((string)($row['holdbehavior'] ?? 'passrtp')); ?>
                <option value="passrtp" <?= $holdValue === 'passrtp' ? 'selected' : '' ?>>Pass RTP</option>
                <option value="pausertp" <?= $holdValue === 'pausertp' ? 'selected' : '' ?>>Pause RTP</option>
                <option value="endcall" <?= $holdValue === 'endcall' ? 'selected' : '' ?>>End Call</option>
            </select>
        </div>
        <button class="button" type="submit">Save SIP Trunk</button>
    </form>
<?php endif; ?>
</body>
</html>
