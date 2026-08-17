<?php

declare(strict_types=1);

if ($argc !== 5) {
    fwrite(STDERR, "Usage: php query.php <server> <port> <name> <ipv4|REFUSED>\n");
    exit(2);
}

[$script, $server, $port, $name, $expected] = $argv;
$id = random_int(1, 65535);
$question = '';

foreach (explode('.', rtrim($name, '.')) as $label) {
    if ($label === '' || strlen($label) > 63) {
        fwrite(STDERR, "Invalid DNS label in {$name}\n");
        exit(2);
    }
    $question .= chr(strlen($label)).$label;
}

$packet = pack('n6', $id, 0x0100, 1, 0, 0, 0).$question."\0".pack('n2', 1, 1);
$socket = socket_create(AF_INET, SOCK_DGRAM, SOL_UDP);
if ($socket === false) {
    throw new RuntimeException(socket_strerror(socket_last_error()));
}

socket_set_option($socket, SOL_SOCKET, SO_RCVTIMEO, ['sec' => 2, 'usec' => 0]);
if (socket_sendto($socket, $packet, strlen($packet), 0, $server, (int) $port) === false) {
    throw new RuntimeException(socket_strerror(socket_last_error($socket)));
}

$response = '';
$source = '';
$sourcePort = 0;
if (socket_recvfrom($socket, $response, 4096, 0, $source, $sourcePort) === false) {
    throw new RuntimeException(socket_strerror(socket_last_error($socket)));
}

$header = unpack('nid/nflags/nquestions/nanswers/nauthority/nadditional', substr($response, 0, 12));
if ($header === false || $header['id'] !== $id) {
    fwrite(STDERR, "Invalid DNS response for {$name}\n");
    exit(1);
}

$rcode = $header['flags'] & 0x000f;
if ($expected === 'REFUSED') {
    if ($rcode !== 5 && $header['answers'] !== 0) {
        fwrite(STDERR, "Expected refusal/no answer for {$name}; rcode={$rcode} answers={$header['answers']}\n");
        exit(1);
    }
    echo "refused {$name}\n";
    exit(0);
}

$packedAddress = inet_pton($expected);
if ($rcode !== 0 || $header['answers'] < 1 || $packedAddress === false || !str_contains($response, $packedAddress)) {
    fwrite(STDERR, "Expected {$name} to resolve to {$expected}; rcode={$rcode} answers={$header['answers']}\n");
    exit(1);
}

echo "resolved {$name} {$expected}\n";

