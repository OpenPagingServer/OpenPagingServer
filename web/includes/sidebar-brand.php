<?php
if (!function_exists('ops_sidebar_h')) {
    function ops_sidebar_h($value) {
        return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8');
    }
}

if (!function_exists('ops_sidebar_truthy')) {
    function ops_sidebar_truthy($value) {
        return in_array(strtolower(trim((string)$value)), ['1', 'true', 'yes', 'on'], true);
    }
}

if (!function_exists('ops_sidebar_brand_html')) {
    function ops_sidebar_brand_html($settings, $productName) {
        $settings = is_array($settings) ? $settings : [];
        $useLogo = ops_sidebar_truthy($settings['use_logo_in_sidebar'] ?? '1');
        $lightLogo = trim((string)($settings['sidebar_logo_light'] ?? '/assets/OPENPAGINGSERVER-768x576-LIGHTMODE.png'));
        $darkLogo = trim((string)($settings['sidebar_logo_dark'] ?? '/assets/OPENPAGINGSERVER-768x576-DARKMODE.png'));
        $productName = (string)$productName;

        if (!$useLogo || $lightLogo === '') {
            return '<div class="sidebar-brand"><span>' . ops_sidebar_h($productName) . '</span></div>';
        }

        $darkSource = $darkLogo !== ''
            ? '<source media="(prefers-color-scheme: dark)" srcset="' . ops_sidebar_h($darkLogo) . '">'
            : '';

        return '<div class="sidebar-brand sidebar-brand-logo"><picture>' .
            $darkSource .
            '<img src="' . ops_sidebar_h($lightLogo) . '" alt="' . ops_sidebar_h($productName) . '">' .
            '</picture></div>';
    }
}
