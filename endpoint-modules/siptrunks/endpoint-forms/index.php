<?php
function siptrunks_form_h($value) { return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8'); }
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}.grid{display:grid;gap:12px}.option{display:block;border:1px solid #ddd;border-radius:6px;padding:14px;text-decoration:none;color:inherit}.option:hover{border-color:#1976D2;background:#F8FBFF}.label{font-weight:500;margin-bottom:4px}.desc{color:#5f6368;font-size:.92em}@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.option{border-color:#333}.option:hover{border-color:#BB86FC;background:#242424}.desc{color:#aaa}}
</style>
</head>
<body>
<div class="grid">
<?php foreach ($forms as $type => $form): ?>
    <a class="option" href="/admin/endpoint-form-frame.php?<?= siptrunks_form_h(http_build_query(['module' => $module, 'type' => $type])) ?>">
        <div class="label"><?= siptrunks_form_h($form['label'] ?? $type) ?></div>
        <div class="desc"><?= siptrunks_form_h($form['description'] ?? '') ?></div>
    </a>
<?php endforeach; ?>
</div>
</body>
</html>
