<?php
return [
    'ip' => [
        'label' => 'IP SIP Trunk',
        'description' => 'Trust SIP requests from a specific trunk IP address.',
        'file' => 'ip.php',
    ],
    'auth' => [
        'label' => 'Authenticated SIP Trunk',
        'description' => 'Authenticate SIP requests with a username and password.',
        'file' => 'auth.php',
    ],
    'dialplan' => [
        'label' => 'SIP Dialplan Extension',
        'description' => 'Route a SIP extension to paging, messaging, test tone, or echo test.',
        'file' => 'dialplan.php',
    ],
];
