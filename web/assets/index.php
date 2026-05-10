<?php
ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
ini_set('log_errors', 1);
ini_set('error_log', '/tmp/php-debug.log');
error_reporting(E_ALL);

session_start();
require_once __DIR__ . '/../config.php';
require_once __DIR__ . '/../includes/sidebar-brand.php';

const OPS_ASSET_DIR = '/var/lib/openpagingserver/assets';
const OPS_MAX_UPLOAD_BYTES = 52428800;

function h($value) {
    return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8');
}

function php_size_to_bytes($value) {
    $value = trim((string)$value);
    if ($value === '') {
        return 0;
    }
    $last = strtolower($value[strlen($value) - 1]);
    $number = (float)$value;
    if ($number <= 0) {
        return 0;
    }
    if ($last === 'g') {
        return (int)($number * 1024 * 1024 * 1024);
    }
    if ($last === 'm') {
        return (int)($number * 1024 * 1024);
    }
    if ($last === 'k') {
        return (int)($number * 1024);
    }
    return (int)$number;
}

function effective_upload_limit_bytes() {
    $limits = [OPS_MAX_UPLOAD_BYTES];
    $uploadMax = php_size_to_bytes(ini_get('upload_max_filesize'));
    $postMax = php_size_to_bytes(ini_get('post_max_size'));
    if ($uploadMax > 0) {
        $limits[] = $uploadMax;
    }
    if ($postMax > 0) {
        $limits[] = $postMax;
    }
    return min($limits);
}

function format_bytes($bytes) {
    $bytes = (int)$bytes;
    if ($bytes >= 1073741824) {
        return number_format($bytes / 1073741824, 2) . ' GB';
    }
    if ($bytes >= 1048576) {
        return number_format($bytes / 1048576, 2) . ' MB';
    }
    if ($bytes >= 1024) {
        return number_format($bytes / 1024, 1) . ' KB';
    }
    return $bytes . ' B';
}

function asset_perms($value) {
    $parts = array_map('trim', explode(',', strtolower((string)$value)));
    return array_filter($parts, function ($part) {
        return $part !== '';
    });
}

function has_asset_perm($value) {
    $perms = asset_perms($value);
    return in_array('all', $perms, true) || in_array('asset-edit', $perms, true);
}

function ensure_asset_dir(&$errors) {
    if (!is_dir(OPS_ASSET_DIR)) {
        if (!@mkdir(OPS_ASSET_DIR, 0755, true) && !is_dir(OPS_ASSET_DIR)) {
            $errors[] = 'Asset directory could not be created.';
            return false;
        }
    }
    return is_dir(OPS_ASSET_DIR) && is_readable(OPS_ASSET_DIR);
}

function asset_filename($name) {
    $name = str_replace(["\0", '/', '\\'], '', (string)$name);
    $name = preg_replace('/[^A-Za-z0-9._ -]/', '_', $name);
    $name = trim($name, " .\t\n\r\0\x0B");
    return $name;
}

function asset_ext($name) {
    return strtolower(pathinfo((string)$name, PATHINFO_EXTENSION));
}

function asset_allowed_ext($name) {
    return in_array(asset_ext($name), ['txt', 'jpg', 'png', 'bmp', 'wav', 'mp3'], true);
}

function asset_path($name) {
    $safe = asset_filename($name);
    if ($safe === '') {
        return [null, 'Invalid filename.'];
    }
    $base = realpath(OPS_ASSET_DIR);
    if (!$base) {
        return [null, 'Asset directory is not available.'];
    }
    $path = $base . DIRECTORY_SEPARATOR . $safe;
    $parent = realpath(dirname($path));
    if (!$parent || $parent !== $base) {
        return [null, 'Invalid asset path.'];
    }
    return [$path, null];
}

function file_mime($path) {
    $finfo = finfo_open(FILEINFO_MIME_TYPE);
    if (!$finfo) {
        return '';
    }
    $mime = finfo_file($finfo, $path) ?: '';
    finfo_close($finfo);
    return strtolower($mime);
}

function starts_with_bytes($path, $bytes) {
    $handle = @fopen($path, 'rb');
    if (!$handle) {
        return false;
    }
    $chunk = fread($handle, strlen($bytes));
    fclose($handle);
    return $chunk === $bytes;
}

function looks_like_text($path) {
    $handle = @fopen($path, 'rb');
    if (!$handle) {
        return false;
    }
    $sample = '';
    while (!feof($handle) && strlen($sample) < 65536) {
        $sample .= fread($handle, 8192);
    }
    fclose($handle);
    if ($sample === '') {
        return true;
    }
    if (strpos($sample, "\0") !== false) {
        return false;
    }
    if (preg_match('/<\?(php|=)?/i', $sample)) {
        return false;
    }
    if (substr($sample, 0, 2) === 'MZ' || substr($sample, 0, 4) === "\x7FELF") {
        return false;
    }
    return preg_match('//u', $sample) === 1;
}

function asset_kind($path, $name) {
    $ext = asset_ext($name);
    $mime = is_file($path) ? file_mime($path) : '';
    if ($ext === 'jpg' && starts_with_bytes($path, "\xFF\xD8\xFF") && in_array($mime, ['image/jpeg', 'image/pjpeg'], true)) {
        return 'image';
    }
    if ($ext === 'png' && starts_with_bytes($path, "\x89PNG\r\n\x1A\n") && $mime === 'image/png') {
        return 'image';
    }
    if ($ext === 'bmp' && starts_with_bytes($path, 'BM') && in_array($mime, ['image/bmp', 'image/x-ms-bmp'], true)) {
        return 'image';
    }
    if ($ext === 'wav') {
        $handle = @fopen($path, 'rb');
        if ($handle) {
            $header = fread($handle, 12);
            fclose($handle);
            if (substr($header, 0, 4) === 'RIFF' && substr($header, 8, 4) === 'WAVE' && in_array($mime, ['audio/wav', 'audio/x-wav', 'audio/wave', 'audio/vnd.wave'], true)) {
                return 'audio';
            }
        }
    }
    if ($ext === 'mp3') {
        $handle = @fopen($path, 'rb');
        if ($handle) {
            $header = fread($handle, 4);
            fclose($handle);
            $hasFrame = strlen($header) >= 2 && ord($header[0]) === 0xFF && ((ord($header[1]) & 0xE0) === 0xE0);
            if ((substr($header, 0, 3) === 'ID3' || $hasFrame) && in_array($mime, ['audio/mpeg', 'audio/mp3', 'audio/mpeg3'], true)) {
                return 'audio';
            }
        }
    }
    if ($ext === 'txt' && looks_like_text($path) && in_array($mime, ['text/plain', 'text/x-php', 'application/octet-stream'], true)) {
        return preg_match('/<\?(php|=)?/i', file_get_contents($path, false, null, 0, 65536) ?: '') ? 'unsupported' : 'text';
    }
    return 'unsupported';
}

function validate_asset_file($path, $name, &$error) {
    $limit = effective_upload_limit_bytes();
    if (!asset_allowed_ext($name)) {
        $error = 'Only txt, jpg, png, bmp, wav, and mp3 files are allowed.';
        return false;
    }
    if (!is_file($path) || filesize($path) > $limit) {
        $error = 'File is missing or too large. Current upload limit is ' . format_bytes($limit) . '.';
        return false;
    }
    $kind = asset_kind($path, $name);
    if ($kind === 'unsupported') {
        $error = 'The file contents do not match an allowed asset type.';
        return false;
    }
    return true;
}

function asset_mime_for_output($kind, $name) {
    $ext = asset_ext($name);
    if ($kind === 'image') {
        return $ext === 'png' ? 'image/png' : ($ext === 'bmp' ? 'image/bmp' : 'image/jpeg');
    }
    if ($kind === 'audio') {
        return $ext === 'wav' ? 'audio/wav' : 'audio/mpeg';
    }
    if ($kind === 'text') {
        return 'text/plain; charset=UTF-8';
    }
    return 'application/octet-stream';
}

function asset_reference_tokens($value) {
    if ($value === null || $value === '') {
        return [];
    }
    return explode(':', (string)$value);
}

function asset_value_has_reference($value, $filename) {
    foreach (asset_reference_tokens($value) as $token) {
        if (trim($token) === $filename) {
            return true;
        }
    }
    return false;
}

function asset_value_replace_reference($value, $oldName, $newName) {
    if ($value === null || $value === '') {
        return $value;
    }
    $tokens = asset_reference_tokens($value);
    $changed = false;
    foreach ($tokens as $index => $token) {
        if (trim($token) === $oldName) {
            $tokens[$index] = $newName;
            $changed = true;
        }
    }
    return $changed ? implode(':', $tokens) : $value;
}

function asset_messages_using($pdo, $filename) {
    $matches = [];
    $stmt = $pdo->query("SELECT messageid, name, icon, image, audio FROM messages");
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
        if (
            asset_value_has_reference($row['icon'] ?? '', $filename) ||
            asset_value_has_reference($row['image'] ?? '', $filename) ||
            asset_value_has_reference($row['audio'] ?? '', $filename)
        ) {
            $matches[] = $row;
        }
    }
    return $matches;
}

function asset_replace_message_references($pdo, $oldName, $newName) {
    $stmt = $pdo->query("SELECT messageid, icon, image, audio FROM messages");
    $update = $pdo->prepare("UPDATE messages SET icon = :icon, image = :image, audio = :audio WHERE messageid = :messageid");
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $oldIcon = $row['icon'] ?? null;
        $oldImage = $row['image'] ?? null;
        $oldAudio = $row['audio'] ?? null;
        $icon = asset_value_replace_reference($oldIcon, $oldName, $newName);
        $image = asset_value_replace_reference($oldImage, $oldName, $newName);
        $audio = asset_value_replace_reference($oldAudio, $oldName, $newName);
        if ($icon === $oldIcon && $image === $oldImage && $audio === $oldAudio) {
            continue;
        }
        $update->execute([
            'icon' => $icon,
            'image' => $image,
            'audio' => $audio,
            'messageid' => $row['messageid'],
        ]);
    }
}

if (!isset($_SESSION['user_id'])) {
    header("Location: /");
    exit;
}

$stmt = $pdo->prepare("SELECT role, userperm, adminperm FROM users WHERE id = :id LIMIT 1");
$stmt->execute(['id' => $_SESSION['user_id']]);
$userData = $stmt->fetch(PDO::FETCH_ASSOC);
if (!$userData) {
    header("Location: /");
    exit;
}

$userRole = $userData['role'] ?? '';
if ($userRole === 'receiver' || $userRole === 'tempreceiver') {
    header("Location: /dashboard.php");
    exit;
}
$isAdmin = ($userRole === 'admin' || $userRole === 'tempadmin');
$canUserEdit = has_asset_perm($userData['userperm'] ?? '');
$canAdminEdit = $isAdmin && has_asset_perm($userData['adminperm'] ?? '');
$canEdit = $canUserEdit || $canAdminEdit;
$canDelete = $canAdminEdit;

$stmt = $pdo->query("SELECT parameter, value FROM systemsettings");
$settings = [];
foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
    $settings[$row['parameter']] = $row['value'];
}
$product_name = $settings['product_name'] ?? 'Open Paging Server';
$favicon = $settings['favicon'] ?? '';
$show_online_docs = $settings['show_online_docs'] ?? '1';

$errors = [];
$messages = [];
ensure_asset_dir($errors);
$effectiveUploadLimit = effective_upload_limit_bytes();

if (isset($_GET['raw'])) {
    [$path, $pathError] = asset_path($_GET['raw']);
    if ($pathError || !is_file($path)) {
        http_response_code(404);
        echo 'File not found';
        exit;
    }
    $name = basename($path);
    $kind = asset_kind($path, $name);
    if (!in_array($kind, ['image', 'audio', 'text'], true)) {
        http_response_code(415);
        echo 'Preview unavailable';
        exit;
    }
    header('Content-Type: ' . asset_mime_for_output($kind, $name));
    header('Content-Length: ' . filesize($path));
    header('Content-Disposition: inline; filename="' . str_replace('"', '', $name) . '"');
    readfile($path);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = $_POST['action'] ?? '';
    if (!$canEdit) {
        $errors[] = 'You do not have permission to edit assets.';
    } elseif ($action === 'upload') {
        if (!isset($_FILES['asset_file'])) {
            $errors[] = 'Choose a file to upload.';
        } elseif (($_FILES['asset_file']['error'] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_INI_SIZE || ($_FILES['asset_file']['error'] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_FORM_SIZE) {
            $errors[] = 'Upload is too large. Current upload limit is ' . format_bytes($effectiveUploadLimit) . '.';
        } elseif (($_FILES['asset_file']['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_OK || !is_uploaded_file($_FILES['asset_file']['tmp_name'])) {
            $errors[] = 'Choose a file to upload.';
        } else {
            $originalName = asset_filename($_FILES['asset_file']['name'] ?? '');
            if ($originalName === '') {
                $errors[] = 'Invalid upload filename.';
            } elseif ($_FILES['asset_file']['size'] > $effectiveUploadLimit) {
                $errors[] = 'Upload is too large. Current upload limit is ' . format_bytes($effectiveUploadLimit) . '.';
            } else {
                $validationError = '';
                if (!validate_asset_file($_FILES['asset_file']['tmp_name'], $originalName, $validationError)) {
                    $errors[] = $validationError;
                } else {
                    [$target, $pathError] = asset_path($originalName);
                    if ($pathError) {
                        $errors[] = $pathError;
                    } elseif (file_exists($target)) {
                        $errors[] = 'An asset with that name already exists.';
                    } elseif (!@move_uploaded_file($_FILES['asset_file']['tmp_name'], $target)) {
                        $errors[] = 'Upload failed.';
                    } else {
                        @chmod($target, 0644);
                        $messages[] = 'Asset uploaded.';
                    }
                }
            }
        }
    } elseif ($action === 'rename') {
        $oldName = asset_filename($_POST['file'] ?? '');
        $newName = asset_filename($_POST['new_name'] ?? '');
        [$oldPath, $oldError] = asset_path($oldName);
        [$newPath, $newError] = asset_path($newName);
        if ($oldError || $newError || $newName === '' || !is_file($oldPath)) {
            $errors[] = 'Rename failed because the asset path is invalid.';
        } elseif (file_exists($newPath)) {
            $errors[] = 'Another asset already uses that name.';
        } else {
            $validationError = '';
            if (!validate_asset_file($oldPath, $newName, $validationError)) {
                $errors[] = $validationError;
            } else {
                try {
                    $pdo->beginTransaction();
                    if (!@rename($oldPath, $newPath)) {
                        throw new RuntimeException('Rename failed.');
                    }
                    asset_replace_message_references($pdo, $oldName, $newName);
                    $pdo->commit();
                    $messages[] = 'Asset renamed.';
                } catch (Throwable $exc) {
                    if ($pdo->inTransaction()) {
                        $pdo->rollBack();
                    }
                    if (is_file($newPath) && !is_file($oldPath)) {
                        @rename($newPath, $oldPath);
                    }
                    $errors[] = $exc->getMessage();
                }
            }
        }
    } elseif ($action === 'delete') {
        if (!$canDelete) {
            $errors[] = 'Only asset-edit in admin permissions can delete assets.';
        } else {
            $name = asset_filename($_POST['file'] ?? '');
            [$path, $pathError] = asset_path($name);
            if ($pathError || !is_file($path)) {
                $errors[] = 'Delete failed because the asset path is invalid.';
            } elseif (!empty(asset_messages_using($pdo, $name))) {
                $errors[] = 'This asset must be removed from all messages that uses it before it can be deleted.';
            } elseif (!@unlink($path)) {
                $errors[] = 'Delete failed.';
            } else {
                $messages[] = 'Asset deleted.';
            }
        }
    }
}

$assets = [];
if (is_dir(OPS_ASSET_DIR) && is_readable(OPS_ASSET_DIR)) {
    foreach (scandir(OPS_ASSET_DIR) as $entry) {
        if ($entry === '.' || $entry === '..') {
            continue;
        }
        [$path, $pathError] = asset_path($entry);
        if ($pathError || !is_file($path)) {
            continue;
        }
        $assets[] = [
            'name' => basename($path),
            'size' => filesize($path),
            'modified' => filemtime($path),
            'kind' => asset_kind($path, basename($path)),
        ];
    }
    usort($assets, function ($a, $b) {
        return strnatcasecmp($a['name'], $b['name']);
    });
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Assets - <?= h($product_name) ?></title>
<?php if (!empty($favicon)): ?>
<link rel="icon" href="<?= h($favicon) ?>" type="image/x-icon">
<?php endif; ?>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet" />
<link href="/assets/sidebar-brand.css" rel="stylesheet" />
<style>
body, html { margin:0; padding:0; font-family:"Tahoma",sans-serif; font-weight:300; background-color:#F8FAFD; height:100%; color:#202124; }
#sidebar { width:220px; background-color:#1976D2; color:#FFF; height:100vh; position:fixed; top:0; left:0; display:flex; flex-direction:column; box-shadow:2px 0 8px rgba(0,0,0,0.2); transition:transform 0.3s ease; z-index:1200; }
@media (max-width:767px){ #sidebar{ transform:translateX(-100%); } #sidebar.open{ transform:translateX(0); } }
#sidebar h2 { text-align:center; padding:20px 0; margin:0; font-weight:500; background-color:#1565C0; font-size:1.2em; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#FFF; padding:12px 20px; display:block; border-bottom:1px solid rgba(255,255,255,0.1); text-decoration:none; transition:background 0.3s; font-size:0.9em; text-align:left; box-sizing:border-box; }
#sidebar a i,.logout-btn i,.logout-btn-mobile i,.admin-only i { margin-right:8px; width:20px; }
#sidebar a:hover,#sidebar a.active{ background-color:#1565C0; }
.logout-btn{ background-color:#C62828; border:none; cursor:pointer; margin-top:auto; transition:background-color 0.3s; }
.logout-btn-mobile{ background-color:#C62828; border:none; cursor:pointer; transition:background-color 0.3s; display:none; }
@media(max-width:767px){ .logout-btn{ display:none; } .logout-btn-mobile{ display:block; } }
#mobile-header{ display:flex; background-color:#1565C0; color:#FFF; padding:calc(12px + env(safe-area-inset-top)) 16px 12px 16px; align-items:center; justify-content:space-between; position:fixed; top:0; left:0; right:0; z-index:1100; }
#mobile-header h2{ margin:0; font-size:1.1em; font-weight:400; color:#FFF; }
#mobile-header .hamburger{ font-size:1.5em; cursor:pointer; }
#overlay{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.3); z-index:900; }
#overlay.active{ display:block; }
#content{ margin-left:220px; padding:28px; min-height:100vh; width:calc(100% - 220px); box-sizing:border-box; transition:margin-left 0.3s ease; }
@media(max-width:767px){ #content{ margin-left:0; width:100%; padding:84px 14px 20px 14px; } }
@media(min-width:768px){ #mobile-header{ display:none; } }
.page-top{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:18px; }
.page-title h1{ margin:0; font-weight:400; font-size:2em; }
.muted{ color:#5F6368; font-size:.92em; }
.toolbar{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.button{ background:#1A73E8; color:#FFF; border:none; border-radius:999px; padding:10px 16px; cursor:pointer; font:inherit; display:inline-flex; align-items:center; gap:8px; text-decoration:none; box-shadow:0 1px 2px rgba(60,64,67,.25); }
.button:hover{ background:#1765CC; }
.button.danger{ background:#C62828; border-radius:8px; box-shadow:none; }
.button.subtle{ background:#FFF; color:#1A73E8; border:1px solid #DADCE0; box-shadow:none; border-radius:8px; }
.success{ background:#E6F4EA; border:1px solid #CEEAD6; color:#137333; padding:12px 14px; border-radius:12px; margin-bottom:14px; }
.error{ background:#FCE8E6; border:1px solid #F6AEA9; color:#A50E0E; padding:12px 14px; border-radius:12px; margin-bottom:14px; }
.asset-grid{ display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:16px; }
.asset-card{ background:#FFF; border:1px solid #DADCE0; border-radius:16px; overflow:visible; position:relative; box-shadow:0 1px 2px rgba(60,64,67,.08); }
.preview-box{ height:150px; background:#F1F3F4; border-radius:16px 16px 0 0; display:flex; align-items:center; justify-content:center; overflow:hidden; color:#5F6368; font-size:2.4em; }
.preview-box img{ width:100%; height:100%; object-fit:cover; }
.preview-box audio{ width:calc(100% - 24px); }
.asset-info{ display:flex; gap:10px; align-items:center; padding:12px 12px 12px 14px; min-height:54px; }
.asset-icon{ color:#5F6368; width:24px; display:flex; justify-content:center; }
.asset-name-wrap{ min-width:0; flex:1; }
.file-name{ font-weight:500; font-size:.95em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.file-meta{ color:#5F6368; font-size:.8em; margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.menu-button{ width:34px; height:34px; border:none; background:transparent; color:#5F6368; border-radius:50%; cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:1em; }
.menu-button:hover{ background:#F1F3F4; }
.asset-menu{ display:none; position:absolute; right:10px; bottom:48px; min-width:220px; background:#FFF; border:1px solid #DADCE0; border-radius:12px; box-shadow:0 8px 24px rgba(60,64,67,.25); z-index:30; overflow:hidden; padding:6px; }
.asset-card.menu-open .asset-menu{ display:block; }
.menu-link,.menu-action{ width:100%; border:none; background:transparent; color:#202124; padding:10px 12px; border-radius:8px; display:flex; align-items:center; gap:10px; text-decoration:none; font:inherit; cursor:pointer; box-sizing:border-box; text-align:left; }
.menu-link:hover,.menu-action:hover{ background:#F1F3F4; }
.menu-action.danger{ color:#B3261E; }
.rename-inline{ padding:8px; display:none; border-top:1px solid #E8EAED; margin-top:4px; }
.rename-inline.active{ display:block; }
.rename-inline .control{ width:100%; margin-bottom:8px; }
.control{ padding:10px; border:1px solid #DADCE0; border-radius:8px; font:inherit; box-sizing:border-box; background:#FFF; color:#202124; }
.empty-state{ background:#FFF; border:1px dashed #DADCE0; border-radius:16px; padding:36px; text-align:center; color:#5F6368; }
.modal-backdrop{ display:none; position:fixed; inset:0; background:rgba(32,33,36,.55); z-index:2000; align-items:center; justify-content:center; padding:20px; box-sizing:border-box; }
.modal-backdrop.active{ display:flex; }
.modal-card{ width:100%; max-width:460px; background:#FFF; border-radius:18px; box-shadow:0 20px 60px rgba(0,0,0,.28); overflow:hidden; }
.modal-header{ display:flex; align-items:center; justify-content:space-between; padding:18px 20px; border-bottom:1px solid #E8EAED; }
.modal-header h2{ margin:0; font-weight:400; font-size:1.25em; }
.modal-close{ border:none; background:transparent; width:36px; height:36px; border-radius:50%; cursor:pointer; color:#5F6368; font-size:1.1em; }
.modal-close:hover{ background:#F1F3F4; }
.modal-body{ padding:20px; }
.upload-box{ border:2px dashed #DADCE0; border-radius:16px; padding:22px; text-align:center; background:#F8FAFD; }
.upload-box input{ width:100%; margin-top:14px; }
.modal-actions{ display:flex; justify-content:flex-end; gap:10px; padding:16px 20px; border-top:1px solid #E8EAED; }
@media(max-width:767px){
.page-top{ align-items:center; }
.page-title h1{ font-size:1.45em; }
.page-title .muted{ display:none; }
.asset-grid{ display:block; }
.asset-card{ display:flex; align-items:center; border-radius:14px; margin-bottom:10px; overflow:visible; }
.preview-box{ width:56px; height:56px; flex:none; border-radius:12px; margin:10px; font-size:1.4em; }
.preview-box audio{ display:none; }
.asset-info{ flex:1; padding:10px 10px 10px 0; min-width:0; }
.asset-icon{ display:none; }
.asset-menu{ position:absolute; right:10px; top:54px; bottom:auto; }
.file-name{ font-size:.96em; }
}
@media(prefers-color-scheme:dark){
body,html{ background-color:#121212; color:#E8EAED; }
#sidebar{ background-color:#424242; }
#sidebar h2{ background-color:#303030; color:#FFF; }
#sidebar a,.logout-btn,.logout-btn-mobile,.admin-only{ color:#E0E0E0; }
#sidebar a.active,#sidebar a:hover{ background-color:#505050; }
#mobile-header{ background-color:#424242; }
#content{ background-color:#121212; }
.asset-card,.modal-card,.empty-state{ background:#1E1E1E; border-color:#333; }
.preview-box,.upload-box{ background:#2A2A2A; color:#BBB; border-color:#444; }
.asset-menu{ background:#242424; border-color:#444; }
.menu-link,.menu-action{ color:#E8EAED; }
.menu-link:hover,.menu-action:hover,.menu-button:hover,.modal-close:hover{ background:#333; }
.file-meta,.muted,.asset-icon,.menu-button,.modal-close{ color:#BBB; }
.control{ background:#171717; border-color:#444; color:#EEE; }
.button{ background:#BB86FC; color:#000; }
.button:hover{ background:#A874E8; }
.button.subtle{ background:transparent; color:#BB86FC; border-color:#BB86FC; }
.button.danger{ background:#CF6679; color:#000; }
.success{ background:#14351A; border-color:#2E7D32; color:#C8E6C9; }
.error{ background:#3B1515; border-color:#6D2A2A; color:#FFCDD2; }
.modal-header,.modal-actions,.rename-inline{ border-color:#333; }
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
    <a href="/bells"><i class="fa-solid fa-bell"></i> Bells</a>
    <a href="/assets/" class="active"><i class="fa-solid fa-folder-open"></i> Assets</a>
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
    <div class="page-top">
        <div class="page-title">
            <h1>Assets</h1>
        </div>
        <div class="toolbar">
            <?php if ($canEdit): ?>
                <button class="button" type="button" onclick="openUploadModal()"><i class="fa-solid fa-plus"></i> Upload</button>
            <?php endif; ?>
        </div>
    </div>

    <?php foreach ($messages as $message): ?><div class="success alert-msg"><?= h($message) ?></div><?php endforeach; ?>
    <?php foreach ($errors as $error): ?><div class="error alert-msg"><?= h($error) ?></div><?php endforeach; ?>

    <?php if (empty($assets)): ?>
        <div class="empty-state">
            <i class="fa-solid fa-folder-open" style="font-size:2.2em;margin-bottom:12px;"></i>
            <div>No assets found.</div>
        </div>
    <?php else: ?>
        <section class="asset-grid">
            <?php foreach ($assets as $asset): ?>
                <?php $rawUrl = '/assets/index.php?raw=' . urlencode($asset['name']); ?>
                <article class="asset-card">
                    <a class="preview-box" href="<?= h($rawUrl) ?>" target="_blank" rel="noopener">
                        <?php if ($asset['kind'] === 'image'): ?>
                            <img src="<?= h($rawUrl) ?>" alt="">
                        <?php elseif ($asset['kind'] === 'audio'): ?>
                            <audio controls src="<?= h($rawUrl) ?>"></audio>
                        <?php elseif ($asset['kind'] === 'text'): ?>
                            <i class="fa-solid fa-file-lines"></i>
                        <?php else: ?>
                            <i class="fa-solid fa-file-circle-question"></i>
                        <?php endif; ?>
                    </a>
                    <div class="asset-info">
                        <div class="asset-icon">
                            <?php if ($asset['kind'] === 'image'): ?>
                                <i class="fa-solid fa-image"></i>
                            <?php elseif ($asset['kind'] === 'audio'): ?>
                                <i class="fa-solid fa-music"></i>
                            <?php elseif ($asset['kind'] === 'text'): ?>
                                <i class="fa-solid fa-file-lines"></i>
                            <?php else: ?>
                                <i class="fa-solid fa-file-circle-question"></i>
                            <?php endif; ?>
                        </div>
                        <div class="asset-name-wrap">
                            <div class="file-name" title="<?= h($asset['name']) ?>"><?= h($asset['name']) ?></div>
                            <div class="file-meta"><?= h(format_bytes($asset['size'])) ?> - <?= h(date('Y-m-d H:i', $asset['modified'])) ?></div>
                        </div>
                        <button class="menu-button" type="button" onclick="toggleAssetMenu(event, this)" aria-label="Asset options"><i class="fa-solid fa-ellipsis-vertical"></i></button>
                    </div>
                    <div class="asset-menu">
                        <a class="menu-link" href="<?= h($rawUrl) ?>" target="_blank" rel="noopener"><i class="fa-solid fa-eye"></i> Open preview</a>
                        <?php if ($canEdit): ?>
                            <button class="menu-action" type="button" onclick="showRenameForm(event, this)"><i class="fa-solid fa-pen"></i> Rename</button>
                            <div class="rename-inline">
                                <form method="post">
                                    <input type="hidden" name="action" value="rename">
                                    <input type="hidden" name="file" value="<?= h($asset['name']) ?>">
                                    <input class="control" name="new_name" value="<?= h($asset['name']) ?>" required>
                                    <button class="button subtle" type="submit"><i class="fa-solid fa-check"></i> Save</button>
                                </form>
                            </div>
                        <?php endif; ?>
                        <?php if ($canDelete): ?>
                            <form method="post" onsubmit="return confirm('Delete this asset?')">
                                <input type="hidden" name="action" value="delete">
                                <input type="hidden" name="file" value="<?= h($asset['name']) ?>">
                                <button class="menu-action danger" type="submit"><i class="fa-solid fa-trash"></i> Delete</button>
                            </form>
                        <?php endif; ?>
                    </div>
                </article>
            <?php endforeach; ?>
        </section>
    <?php endif; ?>
</div>

<?php if ($canEdit): ?>
<div id="upload-modal" class="modal-backdrop" onclick="modalBackdropClick(event)">
    <div class="modal-card">
        <div class="modal-header">
            <h2>Upload asset</h2>
            <button class="modal-close" type="button" onclick="closeUploadModal()"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <form method="post" enctype="multipart/form-data">
            <div class="modal-body">
                <input type="hidden" name="action" value="upload">
                <input type="hidden" name="MAX_FILE_SIZE" value="<?= h($effectiveUploadLimit) ?>">
                <div class="upload-box">
                    <i class="fa-solid fa-cloud-arrow-up" style="font-size:2em;"></i>
                    <div style="margin-top:10px;">Choose an asset to upload</div>
                    <div class="muted" style="margin-top:6px;">Allowed: txt, jpg, png, bmp, wav, mp3. Limit: <?= h(format_bytes($effectiveUploadLimit)) ?>.</div>
                    <input class="control" type="file" name="asset_file" accept=".txt,.jpg,.png,.bmp,.wav,.mp3,text/plain,image/jpeg,image/png,image/bmp,audio/wav,audio/mpeg" required>
                </div>
            </div>
            <div class="modal-actions">
                <button class="button subtle" type="button" onclick="closeUploadModal()">Cancel</button>
                <button class="button" type="submit"><i class="fa-solid fa-upload"></i> Upload</button>
            </div>
        </form>
    </div>
</div>
<?php endif; ?>

<script>
function toggleSidebar() {
  const sidebar = document.getElementById("sidebar");
  sidebar.classList.toggle("open");
  document.getElementById("overlay").classList.toggle("active", sidebar.classList.contains("open"));
}
function closeSidebar() {
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("overlay").classList.remove("active");
}
function closeSidebarOnContentClick() {
  if (document.getElementById("sidebar").classList.contains("open")) closeSidebar();
}
function logout() {
  window.location.href = "/logout.php";
}
function openUploadModal() {
  const modal = document.getElementById("upload-modal");
  if (modal) modal.classList.add("active");
}
function closeUploadModal() {
  const modal = document.getElementById("upload-modal");
  if (modal) modal.classList.remove("active");
}
function modalBackdropClick(event) {
  if (event.target.id === "upload-modal") closeUploadModal();
}
function closeAllAssetMenus(exceptCard) {
  document.querySelectorAll(".asset-card.menu-open").forEach(card => {
    if (card !== exceptCard) card.classList.remove("menu-open");
  });
}
function toggleAssetMenu(event, button) {
  event.stopPropagation();
  const card = button.closest(".asset-card");
  const isOpen = card.classList.contains("menu-open");
  closeAllAssetMenus(card);
  card.classList.toggle("menu-open", !isOpen);
}
function showRenameForm(event, button) {
  event.stopPropagation();
  const menu = button.closest(".asset-menu");
  const form = menu.querySelector(".rename-inline");
  form.classList.toggle("active");
  const input = form.querySelector("input[name='new_name']");
  if (form.classList.contains("active") && input) {
    input.focus();
    input.select();
  }
}
document.addEventListener("click", function(event) {
  if (!event.target.closest(".asset-card")) closeAllAssetMenus(null);
});
document.addEventListener("keydown", function(event) {
  if (event.key === "Escape") {
    closeUploadModal();
    closeAllAssetMenus(null);
  }
});

document.addEventListener("DOMContentLoaded", function() {
  const alerts = document.querySelectorAll('.alert-msg');
  alerts.forEach(function(alert) {
    setTimeout(function() {
      alert.style.transition = "opacity 0.5s ease";
      alert.style.opacity = "0";
      setTimeout(() => alert.remove(), 500);
    }, 5000);
  });
});
</script>
</body>
</html>
