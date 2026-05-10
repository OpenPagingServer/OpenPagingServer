<?php

function message_table_columns($pdo, $table) {
    $stmt = $pdo->query("SHOW COLUMNS FROM `$table`");
    return array_column($stmt->fetchAll(PDO::FETCH_ASSOC), 'Field');
}

function message_runtime_type($type) {
    $type = trim((string)$type);
    $key = strtolower(str_replace([' ', '_', '-'], '', $type));
    $map = [
        'text' => 'TextMessage',
        'audio' => 'AudioMessage',
        'text+audio' => 'Text+AudioMessage',
        'textaudio' => 'Text+AudioMessage',
        'liveaudio' => 'Page',
        'liveaudio+text' => 'Page',
        'liveaudiotext' => 'Page',
        'TextMessage' => 'TextMessage',
        'AudioMessage' => 'AudioMessage',
        'Text+AudioMessage' => 'Text+AudioMessage',
        'textmessage' => 'TextMessage',
        'audiomessage' => 'AudioMessage',
        'text+audiomessage' => 'Text+AudioMessage',
        'textaudiomessage' => 'Text+AudioMessage',
        'Page' => 'Page',
        'page' => 'Page',
    ];
    return $map[$type] ?? $map[$key] ?? 'TextMessage';
}

function message_nonempty($value, $fallback = '') {
    $value = trim((string)$value);
    return $value === '' ? $fallback : $value;
}

function message_multiline_text($value) {
    return str_replace(["\r\n", "\r"], "\n", (string)$value);
}

function message_priority($priority) {
    $priority = message_nonempty($priority, 'Normal');
    $map = [
        'low' => 'Low',
        'normal' => 'Normal',
        'high' => 'High',
        'emergency' => 'Emergency',
    ];
    return $map[strtolower($priority)] ?? 'Normal';
}

function message_parse_expires($expires, $issued) {
    $raw = trim((string)$expires);
    if ($raw === '' || strtolower($raw) === 'manual' || stripos($raw, 'msg=') === 0) {
        return null;
    }
    if (preg_match('/^(\d+)\s*m$/i', $raw, $matches)) {
        $copy = clone $issued;
        $copy->modify('+' . intval($matches[1]) . ' minutes');
        return $copy->format('Y-m-d H:i:s');
    }
    return null;
}

function message_active_broadcast_runtime_dir() {
    if (DIRECTORY_SEPARATOR === '/') {
        return '/tmp/openpagingserver-runtime';
    }
    return dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . 'runtime';
}

function message_active_broadcast_ipc_host() {
    return '127.0.0.1';
}

function message_active_broadcast_ipc_port() {
    return 50000;
}

function message_active_broadcast_normalize_record($record) {
    $normalized = [];
    foreach ((array)$record as $key => $value) {
        if ($value instanceof DateTimeInterface) {
            $normalized[(string)$key] = $value->format('Y-m-d H:i:s');
        } else {
            $normalized[(string)$key] = $value;
        }
    }
    $normalized['id'] = trim((string)($normalized['id'] ?? ''));
    $normalized['delivery'] = trim((string)($normalized['delivery'] ?? 'pending'));
    if ($normalized['delivery'] === '') {
        $normalized['delivery'] = 'pending';
    }
    $normalized['sender'] = trim((string)($normalized['sender'] ?? ''));
    $normalized['groups'] = trim((string)($normalized['groups'] ?? ''));
    $issued = message_active_broadcast_parse_datetime($normalized['issued'] ?? null);
    $normalized['issued'] = $issued ? $issued->format('Y-m-d H:i:s') : (new DateTime())->format('Y-m-d H:i:s');
    $expires = message_active_broadcast_parse_datetime($normalized['expires'] ?? null);
    $normalized['expires'] = $expires ? $expires->format('Y-m-d H:i:s') : null;
    if (array_key_exists('template_id', $normalized) && $normalized['template_id'] !== null && $normalized['template_id'] !== '') {
        $normalized['template_id'] = (string)$normalized['template_id'];
    }
    return $normalized;
}

function message_active_broadcast_parse_datetime($value) {
    if ($value instanceof DateTimeInterface) {
        return $value;
    }
    $text = trim((string)$value);
    if ($text === '') {
        return null;
    }
    $formats = ['Y-m-d H:i:s', 'Y-m-d\TH:i:s'];
    foreach ($formats as $format) {
        $dt = DateTime::createFromFormat($format, $text);
        if ($dt instanceof DateTime) {
            return $dt;
        }
    }
    return null;
}

function message_active_broadcast_ipc_request($command, $payload) {
    $host = message_active_broadcast_ipc_host();
    $port = message_active_broadcast_ipc_port();
    $socket = @stream_socket_client(
        "tcp://{$host}:{$port}",
        $errno,
        $errstr,
        5.0
    );
    if ($socket === false) {
        throw new RuntimeException("Unable to connect to active broadcast IPC server: $errstr");
    }
    stream_set_timeout($socket, 5);
    $encoded = base64_encode(json_encode($payload, JSON_UNESCAPED_SLASHES));
    if ($encoded === false) {
        fclose($socket);
        throw new RuntimeException('Unable to encode active broadcast IPC payload.');
    }
    fwrite($socket, $command . ' ' . $encoded . "\n");
    $responseLine = fgets($socket);
    fclose($socket);
    if ($responseLine === false) {
        throw new RuntimeException('No response from active broadcast IPC server.');
    }
    $decoded = json_decode(trim($responseLine), true);
    if (!is_array($decoded)) {
        throw new RuntimeException('Invalid response from active broadcast IPC server.');
    }
    if (!($decoded['ok'] ?? false)) {
        $error = trim((string)($decoded['error'] ?? 'Unknown IPC error'));
        throw new RuntimeException($error !== '' ? $error : 'Unknown IPC error');
    }
    return $decoded;
}

function message_active_broadcast_write_record($record) {
    $normalized = message_active_broadcast_normalize_record($record);
    if ($normalized['id'] === '') {
        throw new RuntimeException('Active broadcast record requires an id.');
    }
    message_active_broadcast_ipc_request('ACTIVE_STORE', $normalized);
}

function message_active_broadcast_remove_by_template_ids($templateIds, $excludeBroadcastIds = []) {
    $wanted = [];
    foreach ((array)$templateIds as $templateId) {
        $token = trim((string)$templateId);
        if ($token !== '') {
            $wanted[$token] = true;
        }
    }
    if (empty($wanted)) {
        return [];
    }
    $excluded = [];
    foreach ((array)$excludeBroadcastIds as $broadcastId) {
        $token = trim((string)$broadcastId);
        if ($token !== '') {
            $excluded[$token] = true;
        }
    }
    $response = message_active_broadcast_ipc_request('ACTIVE_EXPIRE_TEMPLATE_IDS', [
        'template_ids' => array_keys($wanted),
        'exclude_broadcast_ids' => array_keys($excluded),
    ]);
    $removedIds = $response['removed_ids'] ?? [];
    return is_array($removedIds) ? $removedIds : [];
}

function message_active_broadcast_remove_triggered_by_template($templateId) {
    $token = trim((string)$templateId);
    if ($token === '') {
        return [];
    }
    $response = message_active_broadcast_ipc_request('ACTIVE_EXPIRE_TRIGGERED', [
        'template_id' => $token,
    ]);
    $removedIds = $response['removed_ids'] ?? [];
    return is_array($removedIds) ? $removedIds : [];
}

function message_history_set_delivery($pdo, $broadcastIds, $status) {
    $ids = [];
    foreach ((array)$broadcastIds as $broadcastId) {
        $token = trim((string)$broadcastId);
        if ($token !== '') {
            $ids[] = $token;
        }
    }
    if (empty($ids)) {
        return;
    }
    $columns = message_table_columns($pdo, 'broadcasts');
    if (!in_array('delivery', $columns, true)) {
        return;
    }
    $placeholders = implode(', ', array_fill(0, count($ids), '?'));
    $stmt = $pdo->prepare("UPDATE broadcasts SET delivery = ? WHERE id IN ($placeholders)");
    $stmt->execute(array_merge([$status], $ids));
}

function message_history_fetch_broadcast_record($pdo, $broadcastId, $fallback = []) {
    $wanted = [
        'id',
        'name',
        'shortmessage',
        'longmessage',
        'icon',
        'color',
        'vendor_specific',
        'type',
        'expires',
        'issued',
        'groups',
        'image',
        'audio',
        'sender',
        'priority',
        'delivery',
        'template_id',
        'expires_rule',
    ];
    $columns = message_table_columns($pdo, 'broadcasts');
    $selected = array_values(array_intersect($wanted, $columns));
    if (empty($selected)) {
        return $fallback;
    }
    $columnSql = implode(', ', array_map(function($column) {
        return "`$column`";
    }, $selected));
    $stmt = $pdo->prepare("SELECT $columnSql FROM broadcasts WHERE id = :id LIMIT 1");
    $stmt->execute(['id' => $broadcastId]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if (!is_array($row)) {
        return $fallback;
    }
    $record = $fallback;
    foreach ($row as $column => $value) {
        if ($value !== null) {
            $record[$column] = $value;
        }
    }
    return $record;
}

function message_insert_broadcast($pdo, $values) {
    if (function_exists('random_bytes')) {
        $broadcastId = bin2hex(random_bytes(16));
    } else {
        $broadcastId = str_replace('.', '', uniqid('', true)) . mt_rand(100000, 999999);
    }
    $issued = new DateTime();
    $values = array_merge([
        'id' => $broadcastId,
        'name' => '',
        'shortmessage' => '',
        'longmessage' => '',
        'icon' => '',
        'color' => '',
        'vendor_specific' => '',
        'type' => 'TextMessage',
        'expires' => null,
        'issued' => $issued->format('Y-m-d H:i:s'),
        'groups' => '',
        'image' => '',
        'audio' => '',
        'sender' => '',
        'priority' => 'Normal',
        'delivery' => 'pending',
    ], $values);
    $values['name'] = message_nonempty($values['name'] ?? '', 'Broadcast');
    $values['type'] = message_runtime_type($values['type'] ?? '');
    $values['priority'] = message_priority($values['priority'] ?? 'Normal');

    $columns = message_table_columns($pdo, 'broadcasts');
    $insertColumns = [];
    $params = [];
    foreach ($values as $column => $value) {
        if (in_array($column, $columns, true)) {
            $insertColumns[] = $column;
            $params[$column] = $value;
        }
    }
    if (empty($insertColumns)) {
        throw new RuntimeException("Broadcasts table has no insertable columns");
    }

    $columnSql = implode(', ', array_map(function($column) {
        return "`$column`";
    }, $insertColumns));
    $placeholderSql = ':' . implode(', :', $insertColumns);
    $stmt = $pdo->prepare("INSERT INTO broadcasts ($columnSql) VALUES ($placeholderSql)");
    $stmt->execute($params);
    try {
        $activeRecord = message_history_fetch_broadcast_record($pdo, $broadcastId, $values);
        message_active_broadcast_write_record($activeRecord);
    } catch (Throwable $e) {
        message_history_set_delivery($pdo, [$broadcastId], 'failed');
        throw $e;
    }
    return $broadcastId;
}

function message_expire_triggered_broadcasts($pdo, $messageId) {
    $removedIds = message_active_broadcast_remove_triggered_by_template($messageId);
    message_history_set_delivery($pdo, $removedIds, 'expired');
}

function message_expire_message_rule_broadcasts($pdo, $expiresRule, $excludeBroadcastIds = []) {
    $raw = trim((string)$expiresRule);
    if (stripos($raw, 'msg=') !== 0) {
        return;
    }
    $templateIds = array_filter(array_map('trim', explode('.', substr($raw, 4))), 'strlen');
    if (empty($templateIds)) {
        return;
    }
    $removedIds = message_active_broadcast_remove_by_template_ids($templateIds, $excludeBroadcastIds);
    message_history_set_delivery($pdo, $removedIds, 'expired');
}

function message_create_broadcast_from_template($pdo, $messageId, $groups, $sender) {
    $columns = message_table_columns($pdo, 'messages');
    $wanted = ['messageid', 'name', 'shortmessage', 'longmessage', 'icon', 'color', 'type', 'expires', 'image', 'audio', 'priority', 'vendor_specific'];
    $selected = array_values(array_intersect($wanted, $columns));
    if (empty($selected)) {
        throw new RuntimeException("Messages table has no selectable columns");
    }
    $columnSql = implode(', ', array_map(function($column) {
        return "`$column`";
    }, $selected));
    $stmt = $pdo->prepare("SELECT $columnSql FROM messages WHERE messageid = :id LIMIT 1");
    $stmt->execute(['id' => $messageId]);
    $template = $stmt->fetch(PDO::FETCH_ASSOC);
    if (!$template) {
        throw new RuntimeException("Message not found");
    }

    $issued = new DateTime();
    $expiresRule = trim((string)($template['expires'] ?? ''));
    $expiresAt = message_parse_expires($expiresRule, $issued);
    $broadcastId = message_insert_broadcast($pdo, [
        'name' => message_nonempty($template['name'] ?? '', 'Broadcast'),
        'shortmessage' => $template['shortmessage'] ?? '',
        'longmessage' => message_multiline_text($template['longmessage'] ?? ''),
        'icon' => $template['icon'] ?? '',
        'color' => $template['color'] ?? '',
        'vendor_specific' => $template['vendor_specific'] ?? '',
        'template_id' => message_nonempty($template['messageid'] ?? '', $messageId),
        'expires_rule' => $expiresRule,
        'type' => message_runtime_type($template['type'] ?? ''),
        'expires' => $expiresAt,
        'issued' => $issued->format('Y-m-d H:i:s'),
        'groups' => $groups,
        'image' => $template['image'] ?? '',
        'audio' => $template['audio'] ?? '',
        'sender' => $sender,
        'priority' => message_priority($template['priority'] ?? 'Normal'),
    ]);
    message_expire_message_rule_broadcasts($pdo, $expiresRule, [$broadcastId]);
    message_expire_triggered_broadcasts($pdo, $messageId);
    return $broadcastId;
}
