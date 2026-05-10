<?php
function siptrunks_ip_h($value) { return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8'); }

$message = '';
$error = '';
$values = ['name' => '', 'ipaddr' => ''];

try {
    $pdo->exec("CREATE TABLE IF NOT EXISTS `sip-trunks` (`id` INT NOT NULL AUTO_INCREMENT, `name` VARCHAR(255) NOT NULL DEFAULT '', `auth` ENUM('IP','USERPASS') NOT NULL DEFAULT 'IP', `username` VARCHAR(255) DEFAULT NULL, `password` VARCHAR(255) DEFAULT NULL, `ipaddr` VARCHAR(255) NOT NULL DEFAULT '0.0.0.0', `status` VARCHAR(255) NOT NULL DEFAULT 'Offline', `holdbehavior` ENUM('passrtp','pausertp','endcall') NOT NULL DEFAULT 'passrtp', PRIMARY KEY (`id`), KEY `auth_idx` (`auth`), KEY `username_idx` (`username`), KEY `ipaddr_idx` (`ipaddr`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci");

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $values['name'] = trim((string)($_POST['name'] ?? ''));
        $values['ipaddr'] = trim((string)($_POST['ipaddr'] ?? ''));
        if ($values['name'] === '' || $values['ipaddr'] === '') {
            throw new RuntimeException('Name and IP address are required.');
        }
        if (!filter_var($values['ipaddr'], FILTER_VALIDATE_IP)) {
            throw new RuntimeException('Enter a valid IP address.');
        }
        $stmt = $pdo->prepare("SELECT COUNT(*) FROM `sip-trunks` WHERE `auth` = 'IP' AND `ipaddr` = :ipaddr");
        $stmt->execute(['ipaddr' => $values['ipaddr']]);
        if ((int)$stmt->fetchColumn() > 0) {
            throw new RuntimeException('That SIP trunk IP already exists.');
        }
        $stmt = $pdo->prepare("INSERT INTO `sip-trunks` (`name`, `auth`, `username`, `password`, `ipaddr`, `status`) VALUES (:name, 'IP', NULL, NULL, :ipaddr, 'Offline')");
        $stmt->execute(['name' => $values['name'], 'ipaddr' => $values['ipaddr']]);
        $message = 'IP SIP trunk added.';
        $values = ['name' => '', 'ipaddr' => ''];
    }
} catch (Throwable $exc) {
    $error = $exc->getMessage();
}
?>
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}.grid{display:grid;gap:12px}.row{display:grid;gap:6px}label{font-weight:500}.control{padding:10px;border:1px solid #ddd;border-radius:4px;font:inherit}.button{background:#1976D2;color:#fff;border:0;border-radius:4px;padding:10px 14px;font:inherit;cursor:pointer}.success{background:#E8F5E9;border:1px solid #A5D6A7;color:#1B5E20;padding:10px;border-radius:6px;margin-bottom:12px}.error{background:#FFEBEE;border:1px solid #EF9A9A;color:#B71C1C;padding:10px;border-radius:6px;margin-bottom:12px}@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.control{background:#171717;border-color:#333;color:#eee}.button{background:#BB86FC;color:#000}}</style></head><body>
<?php if ($message): ?><div class="success"><?= siptrunks_ip_h($message) ?></div><?php endif; ?><?php if ($error): ?><div class="error"><?= siptrunks_ip_h($error) ?></div><?php endif; ?>
<form method="post" class="grid">
    <div class="row"><label>Name</label><input class="control" name="name" value="<?= siptrunks_ip_h($values['name']) ?>" required></div>
    <div class="row"><label>IP Address</label><input class="control" name="ipaddr" value="<?= siptrunks_ip_h($values['ipaddr']) ?>" required></div>
    <button class="button" type="submit">Add IP SIP Trunk</button>
</form>
</body></html>
