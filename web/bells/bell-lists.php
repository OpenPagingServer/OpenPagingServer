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

bells_handle_list_editor_post($pdo, 0, '/bells/bell-lists.php');

bells_begin_page($settings, $user, 'Bell Lists');
bells_page_header(
    'Bell Lists',
    'System-wide lists that can be used by any schedule',
    '<a class="btn secondary" href="/bells"><i class="fa-solid fa-arrow-left"></i> Back</a>'
);
?>

<div class="summary-grid">
    <a class="summary-item" href="/bells/bell-lists.php" style="text-decoration:none;color:inherit;">
        <strong><i class="fa-solid fa-list-check"></i></strong>
        <span class="muted">Bell Lists</span>
    </a>
    <a class="summary-item" href="/bells" style="text-decoration:none;color:inherit;">
        <strong><i class="fa-solid fa-calendar-days"></i></strong>
        <span class="muted">Schedules</span>
    </a>
</div>

<?php bells_render_list_editor($pdo, 0, '/bells/bell-lists.php'); ?>

<?php bells_end_page(); ?>
