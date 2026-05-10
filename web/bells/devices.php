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

[$endpointData, $endpointError] = bells_endpoint_manager_request('LIST_ENDPOINTS');
$devices = [];
$moduleCounts = [];
if (is_array($endpointData)) {
    foreach (($endpointData['modules'] ?? []) as $moduleInfo) {
        $moduleName = $moduleInfo['module'] ?? '';
        $displayName = $moduleInfo['display_name'] ?? $moduleName;
        $moduleCounts[$displayName] = 0;
        foreach (($moduleInfo['endpoints'] ?? []) as $endpoint) {
            if (!bells_output_capable($endpoint)) {
                continue;
            }
            $moduleCounts[$displayName]++;
            $devices[] = [
                'module' => $displayName,
                'name' => $endpoint['name'] ?? $endpoint['id'] ?? 'Endpoint',
                'address' => $endpoint['address'] ?? '',
                'model' => $endpoint['model'] ?? '',
                'status' => $endpoint['status'] ?? 'Unknown',
                'type' => $endpoint['type'] ?? '',
            ];
        }
    }
}

usort($devices, function ($a, $b) {
    return strnatcasecmp(($a['name'] ?? ''), ($b['name'] ?? ''));
});

bells_begin_page($settings, $user, 'Bell-Capable Devices');
bells_page_header(
    'Bell-Capable Devices',
    'Output endpoints currently advertised for bells',
    '<a class="btn secondary" href="/bells"><i class="fa-solid fa-arrow-left"></i> Back</a>'
);
?>

<?php if ($endpointError): ?><div class="error"><?= htmlspecialchars($endpointError) ?></div><?php endif; ?>

<div class="summary-grid">
    <div class="summary-item">
        <strong><?= htmlspecialchars((string)count($devices)) ?></strong>
        <span class="muted">Devices</span>
    </div>
    <?php foreach ($moduleCounts as $moduleName => $count): ?>
        <div class="summary-item">
            <strong><?= htmlspecialchars((string)$count) ?></strong>
            <span class="muted"><?= htmlspecialchars($moduleName) ?></span>
        </div>
    <?php endforeach; ?>
</div>

<div class="info-card flush">
    <?php if (empty($devices)): ?>
        <p class="muted" style="text-align:center;padding:20px;">No bell-capable devices found.</p>
    <?php else: ?>
        <ul class="list">
            <?php foreach ($devices as $device): ?>
                <li class="list-item">
                    <div class="list-main">
                        <div class="list-title"><?= htmlspecialchars($device['name']) ?></div>
                        <div class="list-meta">
                            <?= htmlspecialchars($device['module']) ?> ·
                            <?= htmlspecialchars($device['type']) ?> ·
                            <?= htmlspecialchars($device['model']) ?>
                            <?php if ($device['address'] !== ''): ?> · <?= htmlspecialchars($device['address']) ?><?php endif; ?>
                        </div>
                    </div>
                    <span class="muted"><?= htmlspecialchars($device['status']) ?></span>
                </li>
            <?php endforeach; ?>
        </ul>
    <?php endif; ?>
</div>

<?php bells_end_page(); ?>
