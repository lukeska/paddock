<?php

declare(strict_types=1);

$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
$candidate = ($_SERVER['DOCUMENT_ROOT'] ?? '').$path;

if ($path !== '/' && is_file($candidate)) {
    return false;
}

require $_SERVER['DOCUMENT_ROOT'].'/index.php';
