<?php

declare(strict_types=1);

if (!defined('PADDOCK_FIXTURE_ID')) {
    http_response_code(500);
    header('Content-Type: application/json');
    echo json_encode(['error' => 'fixture identity is not configured'], JSON_THROW_ON_ERROR);
    exit;
}

header('X-Paddock-Fixture: '.PADDOCK_FIXTURE_ID);

$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';

if ($path === '/health') {
    header('Content-Type: text/plain; charset=utf-8');
    echo "ok ".PADDOCK_FIXTURE_ID."\n";
    exit;
}

if ($path === '/runtime') {
    respond([
        'fixture' => PADDOCK_FIXTURE_ID,
        'php_version' => PHP_VERSION,
        'php_version_id' => PHP_VERSION_ID,
        'sapi' => PHP_SAPI,
        'document_root' => $_SERVER['DOCUMENT_ROOT'] ?? null,
        'request' => [
            'method' => $_SERVER['REQUEST_METHOD'] ?? null,
            'host' => $_SERVER['HTTP_HOST'] ?? null,
            'path' => $path,
            'forwarded_proto' => $_SERVER['HTTP_X_FORWARDED_PROTO'] ?? null,
            'forwarded_for' => $_SERVER['HTTP_X_FORWARDED_FOR'] ?? null,
        ],
    ]);
}

if ($path === '/failure') {
    error_log('Paddock fixture deliberate failure: '.PADDOCK_FIXTURE_ID);
    respond([
        'fixture' => PADDOCK_FIXTURE_ID,
        'error' => 'deliberate fixture failure',
    ], 500);
}

respond([
    'fixture' => PADDOCK_FIXTURE_ID,
    'route' => $path,
    'message' => 'front controller reached',
]);

/**
 * @param array<string, mixed> $payload
 */
function respond(array $payload, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(
        $payload,
        JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR,
    )."\n";
    exit;
}
