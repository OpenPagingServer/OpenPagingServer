// This is very, very temporary!!!

<?php
ini_set('display_errors', 1);
error_reporting(E_ALL);

session_start();
require_once '/var/www/html/config.php';

if (!isset($_SESSION['user_id'])) {
    header("Location: /");
    exit;
}

if (!isset($_GET['msgid'])) {
    header("Location: /messages");
    exit;
}

$id = escapeshellarg($_GET['msgid']);

exec("nohup /usr/bin/python3 /opt/openpagingserver/msgsendprototype.py $id > /dev/null 2>&1 &");

header("Location: /messages");
exit;
?>
