<?php
/*
 * Writing acceptance probe for a served Paddock site.
 *
 * The `paddock-verify` fixtures only ever echoed PHP_VERSION, so they passed
 * happily while `ProtectHome=read-only` made every real project unwritable:
 * two full reboot-acceptance rounds missed it and the first real Laravel
 * application found it immediately. This exercises what a framework actually
 * does to its own tree, and asserts the other half of the same contract, that
 * the paths ADR 0005's amendment hides really are hidden.
 *
 * Output is one `key=value` per line and a final RESULT line, so a shell
 * runner can grep it without parsing HTML.
 */

$root = dirname(__DIR__);
$results = [];

// A framework creates nested directories under its own root.
$views = $root . '/storage/framework/views';
$logs = $root . '/storage/logs';
$results['mkdir'] = 'ok';
foreach ([$views, $logs] as $directory) {
    if (!is_dir($directory) && !@mkdir($directory, 0755, true)) {
        $results['mkdir'] = 'fail';
    }
}

// Write, read back, and append, the way compiled views and logs behave.
$file = $views . '/probe.txt';
$token = 'paddock-' . getmypid();
$results['write'] = @file_put_contents($file, $token) !== false ? 'ok' : 'fail';
$results['read'] = @file_get_contents($file) === $token ? 'ok' : 'fail';
// Append twice and require growth: a log handler opens in append mode, and a
// single write would pass even if FILE_APPEND silently truncated.
$log = $logs . '/probe.log';
@unlink($log);
$first = @file_put_contents($log, $token . "\n", FILE_APPEND);
$second = @file_put_contents($log, $token . "\n", FILE_APPEND);
$results['append'] = ($first !== false && $second !== false
    && @filesize($log) === strlen($token . "\n") * 2) ? 'ok' : 'fail';

// The exact signature of the read-only-home defect: tempnam() silently falls
// back to the private /tmp and emits a notice the framework turns into a 500.
$temporary = @tempnam($views, 'probe');
$results['tempnam'] = ($temporary && strpos($temporary, $views) === 0) ? 'ok' : 'fell-back';
if ($temporary) {
    @unlink($temporary);
}

// The denials must hold, but they are mount-namespace properties of the
// php-fpm unit, so they only exist when this is served. Run from the CLI there
// is no namespace and these paths are legitimately readable; reporting that as
// a failure would confuse "sandbox absent" with "sandbox broken".
//
// A path that simply does not exist also reads as unreadable, which is
// acceptable: the unit lists these with a `-` prefix because absence is
// allowed.
$home = getenv('HOME') ?: '';
if (PHP_SAPI === 'fpm-fcgi') {
    $results['hidden-ssh'] = @scandir($home . '/.ssh') === false ? 'ok' : 'READABLE';
    $results['hidden-gnupg'] = @scandir($home . '/.gnupg') === false ? 'ok' : 'READABLE';
    $results['hidden-ca'] =
        @file_get_contents($home . '/.local/share/paddock/pki/rootCA-key.pem') === false
            ? 'ok' : 'READABLE';
} else {
    $results['hidden-ssh'] = 'skipped-' . PHP_SAPI;
    $results['hidden-gnupg'] = 'skipped-' . PHP_SAPI;
    $results['hidden-ca'] = 'skipped-' . PHP_SAPI;
}

// php-fpm loads its interpreter from here, so the denial must not be too wide.
$results['runtimes'] =
    @scandir($home . '/.local/share/paddock/runtimes') !== false ? 'ok' : 'fail';

// Leave nothing behind but the directories, which are idempotent to create.
@unlink($file);
@unlink($log);

$results['php'] = PHP_VERSION;
$results['sapi'] = PHP_SAPI;
$bad = array_intersect(['fail', 'fell-back', 'READABLE'], $results);

ksort($results);
foreach ($results as $key => $value) {
    printf("%s=%s\n", $key, $value);
}
echo $bad ? "RESULT=FAIL\n" : "RESULT=PASS\n";
