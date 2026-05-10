<?php
function siptrunks_delete_h($value) {
    return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8');
}

$message = '';
$error = '';
$row = null;

try {
    if (strpos($endpointId, 'dialplan-') === 0) {
        $id = (int)substr($endpointId, strlen('dialplan-'));
        if ($id < 1) {
            throw new RuntimeException('Invalid SIP dialplan extension.');
        }
        $pdo->exec("CREATE TABLE IF NOT EXISTS `endpoints-input-siptrunk` (`id` INT NOT NULL AUTO_INCREMENT, `name` VARCHAR(255) NOT NULL DEFAULT '', `extension` VARCHAR(100) NOT NULL DEFAULT '', `group` VARCHAR(255) DEFAULT NULL, `trigger` VARCHAR(100) NOT NULL DEFAULT 'page', `passcode` VARCHAR(64) DEFAULT NULL, PRIMARY KEY (`id`), KEY `extension_idx` (`extension`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci");
        $stmt = $pdo->prepare("SELECT `id`, `name`, `extension`, `group`, `trigger`, `passcode` FROM `endpoints-input-siptrunk` WHERE `id` = :id");
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        if (!$row) {
            throw new RuntimeException('SIP dialplan extension not found.');
        }
        if ($_SERVER['REQUEST_METHOD'] === 'POST') {
            $stmt = $pdo->prepare("DELETE FROM `endpoints-input-siptrunk` WHERE `id` = :id");
            $stmt->execute(['id' => $id]);
            $message = 'SIP dialplan extension deleted.';
            $row = null;
        }
        ?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}.grid{display:grid;gap:12px}.button{background:#C62828;color:#fff;border:0;border-radius:4px;padding:10px 14px;font:inherit;cursor:pointer}.success{background:#E8F5E9;border:1px solid #A5D6A7;color:#1B5E20;padding:10px;border-radius:6px;margin-bottom:12px}.error{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}.meta{color:#5f6368;margin:0 0 14px}@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.meta{color:#aaa}}
</style>
</head>
<body>
<?php if ($message): ?><div class="success"><?= siptrunks_delete_h($message) ?></div><?php endif; ?>
<?php if ($error): ?><div class="error"><?= siptrunks_delete_h($error) ?></div><?php endif; ?>
<?php if ($row): ?>
    <form method="post" class="grid">
        <p class="meta">Delete <?= siptrunks_delete_h($row['name'] ?? '') ?>?</p>
        <div>SIP Trunk Extension <?php if (!empty($row['extension'])): ?>(<?= siptrunks_delete_h($row['extension']) ?>)<?php endif; ?></div>
        <button class="button" type="submit">Delete SIP Dialplan Extension</button>
    </form>
<?php endif; ?>
</body>
</html>
<?php
        return;
    }

    if (strpos($endpointId, 'trunk-') !== 0) {
        throw new RuntimeException('Invalid SIP trunk endpoint.');
    }
    $id = (int)substr($endpointId, strlen('trunk-'));
    if ($id < 1) {
        throw new RuntimeException('Invalid SIP trunk endpoint.');
    }
    $stmt = $pdo->prepare("SELECT `id`, `name`, `auth`, `username`, `ipaddr`, `status` FROM `sip-trunks` WHERE `id` = :id");
    $stmt->execute(['id' => $id]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if (!$row) {
        throw new RuntimeException('SIP trunk not found.');
    }
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $stmt = $pdo->prepare("DELETE FROM `sip-trunks` WHERE `id` = :id");
        $stmt->execute(['id' => $id]);
        $message = 'SIP trunk deleted.';
        $row = null;
    }
} catch (Throwable $exc) {
    $error = $exc->getMessage();
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}.grid{display:grid;gap:12px}.button{background:#C62828;color:#fff;border:0;border-radius:4px;padding:10px 14px;font:inherit;cursor:pointer}.success{background:#E8F5E9;border:1px solid #A5D6A7;color:#1B5E20;padding:10px;border-radius:6px;margin-bottom:12px}.error{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}.meta{color:#5f6368;margin:0 0 14px}@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.meta{color:#aaa}}
</style>
</head>
<body>
<?php if ($message): ?><div class="success"><?= siptrunks_delete_h($message) ?></div><?php endif; ?>
<?php if ($error): ?><div class="error"><?= siptrunks_delete_h($error) ?></div><?php endif; ?>
<?php if ($row): ?>
    <form method="post" class="grid">
        <p class="meta">Delete <?= siptrunks_delete_h($row['name'] ?? '') ?>?</p>
        <div>
            <?= siptrunks_delete_h(($row['auth'] ?? '') === 'USERPASS' ? 'Authenticated SIP Trunk' : 'SIP Trunk') ?>
            <?php if (!empty($row['ipaddr'])): ?>(<?= siptrunks_delete_h($row['ipaddr']) ?>)<?php endif; ?>
        </div>
        <button class="button" type="submit">Delete SIP Trunk</button>
    </form>
<?php endif; ?>
</body>
</html>
