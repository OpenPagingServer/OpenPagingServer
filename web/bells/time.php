<?php
require_once __DIR__ . '/bell_helpers.php';

header('Content-Type: application/json');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');

$timezoneId = bells_server_timezone_id();
$timezone = new DateTimeZone($timezoneId);
$now = new DateTime('now', $timezone);
$uses12Hour = bells_uses_12_hour_clock();
echo json_encode([
    'date' => $now->format('l, F j, Y'),
    'time' => $now->format($uses12Hour ? 'g:i:s A' : 'H:i:s'),
    'iso' => $now->format(DateTimeInterface::ATOM),
    'timezone' => $timezoneId,
    'uses_12_hour' => $uses12Hour,
    'timestamp_ms' => ((int)$now->format('U')) * 1000,
]);
