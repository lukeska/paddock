<?php

namespace App\PaddockLab\Probes;

use App\Jobs\PaddockProbeJob;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Mail;
use Illuminate\Support\Facades\Storage;
use Throwable;

final class LabRunner
{
    public static function run(bool $includeReverb = false): array
    {
        $checks = [];
        $checks['php'] = self::check('PHP', function () {
            $required = ['curl', 'dom', 'fileinfo', 'filter', 'intl', 'mbstring', 'openssl', 'pdo', 'session', 'tokenizer', 'xml', 'zip'];
            $missing = array_values(array_filter($required, function ($extension) { return ! extension_loaded($extension); }));
            if ($missing) {
                throw new \RuntimeException('Missing extensions: '.implode(', ', $missing));
            }
            return ['version' => PHP_VERSION, 'details' => PHP_SAPI.'; all required extensions loaded'];
        });
        $checks['node'] = self::check('Node', function () {
            $path = public_path('build/node-version.json');
            if (! is_file($path)) throw new \RuntimeException('Frontend build metadata is missing');
            $value = json_decode(file_get_contents($path), true, 512, JSON_THROW_ON_ERROR);
            return ['version' => ltrim($value['node'], 'v'), 'details' => 'Vite asset metadata loaded'];
        });
        $checks['http'] = self::check('HTTP / HTTPS', function () {
            if (! request()) throw new \RuntimeException('Run the HTTP endpoint for routing details');
            return [
                'version' => request()->getScheme(),
                'details' => request()->getHost().' routed to '.public_path(),
            ];
        });
        foreach (['mysql_lab' => 'MySQL', 'pgsql_lab' => 'PostgreSQL'] as $connection => $label) {
            $checks[$connection] = self::check($label, function () use ($connection) {
                $table = 'paddock_lab_probe';
                DB::connection($connection)->statement("CREATE TABLE IF NOT EXISTS {$table} (token VARCHAR(80) PRIMARY KEY)");
                $token = bin2hex(random_bytes(12));
                DB::connection($connection)->table($table)->insert(['token' => $token]);
                $found = DB::connection($connection)->table($table)->where('token', $token)->value('token');
                DB::connection($connection)->table($table)->where('token', $token)->delete();
                if ($found !== $token) throw new \RuntimeException('round trip returned the wrong token');
                return ['version' => (string) DB::connection($connection)->selectOne('select version() as version')->version, 'details' => 'write/read/delete passed'];
            });
        }
        $checks['redis'] = self::check('Redis', function () {
            $token = bin2hex(random_bytes(12));
            Cache::store('redis')->put('paddock-lab:cache', $token, 30);
            if (Cache::store('redis')->pull('paddock-lab:cache') !== $token) throw new \RuntimeException('cache round trip failed');
            return ['version' => null, 'details' => 'Laravel cache write/read/delete passed'];
        });
        $checks['mailpit'] = self::check('Mailpit', function () {
            $token = 'paddock-lab-'.bin2hex(random_bytes(8));
            Mail::raw($token, function ($message) use ($token) { $message->to('lab@paddock.test')->subject($token); });
            $result = self::http('GET', env('MAILPIT_API', 'http://127.0.0.1:18025/api/v1').'/search?query='.rawurlencode($token));
            if (strpos($result['body'], $token) === false) throw new \RuntimeException('message was not found through the Mailpit API');
            return ['version' => null, 'details' => 'SMTP send and API lookup passed', 'url' => env('MAILPIT_UI', 'http://127.0.0.1:18025')];
        });
        $checks['meilisearch'] = self::check('Meilisearch', function () {
            $index = env('PADDOCK_LAB_NAMESPACE', 'paddock_lab').'_probe';
            $token = bin2hex(random_bytes(8));
            self::http('POST', env('MEILISEARCH_HOST').'/indexes/'.$index.'/documents?primaryKey=id', json_encode([['id' => $token, 'value' => $token]]));
            usleep(300000);
            $search = self::http('POST', env('MEILISEARCH_HOST').'/indexes/'.$index.'/search', json_encode(['q' => $token]));
            self::http('DELETE', env('MEILISEARCH_HOST').'/indexes/'.$index);
            if (strpos($search['body'], $token) === false) throw new \RuntimeException('search did not return the probe document');
            return ['version' => null, 'details' => 'index/add/search/delete passed', 'url' => env('MEILISEARCH_HOST')];
        });
        $checks['typesense'] = self::check('Typesense', function () {
            $collection = env('PADDOCK_LAB_NAMESPACE', 'paddock_lab').'_probe';
            $base = env('TYPESENSE_PROTOCOL', 'http').'://'.env('TYPESENSE_HOST').':'.env('TYPESENSE_PORT');
            $headers = ['X-TYPESENSE-API-KEY: '.env('TYPESENSE_API_KEY')];
            self::http('DELETE', $base.'/collections/'.$collection, null, $headers, true);
            self::http('POST', $base.'/collections', json_encode(['name' => $collection, 'fields' => [['name' => 'value', 'type' => 'string']]]), $headers);
            $token = bin2hex(random_bytes(8));
            self::http('POST', $base.'/collections/'.$collection.'/documents', json_encode(['id' => $token, 'value' => $token]), $headers);
            $search = self::http('GET', $base.'/collections/'.$collection.'/documents/search?q='.$token.'&query_by=value', null, $headers);
            self::http('DELETE', $base.'/collections/'.$collection, null, $headers, true);
            if (strpos($search['body'], $token) === false) throw new \RuntimeException('search did not return the probe document');
            return ['version' => null, 'details' => 'collection/add/search/delete passed'];
        });
        $checks['rustfs'] = self::check('RustFS', function () {
            $disk = Storage::disk('s3');
            try { $disk->getClient()->createBucket(['Bucket' => config('filesystems.disks.s3.bucket')]); } catch (Throwable $ignored) {}
            $path = env('PADDOCK_LAB_NAMESPACE', 'paddock_lab').'/probe-'.bin2hex(random_bytes(8));
            $token = bin2hex(random_bytes(24));
            $disk->put($path, $token);
            $found = $disk->get($path);
            $disk->delete($path);
            if ($found !== $token) throw new \RuntimeException('object contents did not match');
            return ['version' => null, 'details' => 'put/get/compare/delete passed', 'url' => env('RUSTFS_UI', 'http://127.0.0.1:19001/rustfs/console/')];
        });
        $checks['queue'] = self::check('Queue', function () {
            $token = bin2hex(random_bytes(12));
            Cache::store('redis')->forget('paddock-lab:queue:'.$token);
            PaddockProbeJob::dispatch($token);
            $deadline = microtime(true) + 8;
            while (microtime(true) < $deadline) {
                if (Cache::store('redis')->pull('paddock-lab:queue:'.$token)) return ['version' => null, 'details' => 'job completed'];
                usleep(200000);
            }
            throw new \RuntimeException('queue worker did not complete the job within 8 seconds');
        });
        $checks['scheduler'] = self::check('Scheduler', function () {
            $heartbeat = Cache::store('redis')->get('paddock-lab:scheduler');
            if (! $heartbeat) throw new \RuntimeException('scheduler heartbeat has not been recorded');
            $age = time() - (int) $heartbeat;
            if ($age > 120) throw new \RuntimeException("scheduler heartbeat is {$age}s old");
            return ['version' => null, 'details' => "heartbeat {$age}s ago"];
        });
        if ($includeReverb) {
            $checks['reverb'] = self::check('Reverb', function () {
                $port = (int) env('REVERB_PORT', 18080);
                $socket = @fsockopen(env('REVERB_HOST', '127.0.0.1'), $port, $errno, $error, 2);
                if (! $socket) throw new \RuntimeException("WebSocket port {$port} unavailable: {$error}");
                fclose($socket);
                return ['version' => null, 'details' => "worker reachable on port {$port}; browser Echo probe enabled"];
            });
        }
        return ['application' => env('PADDOCK_LAB_NAMESPACE'), 'generated_at' => date(DATE_ATOM), 'checks' => $checks];
    }

    public static function reset(): void
    {
        foreach (['mysql_lab', 'pgsql_lab'] as $connection) {
            DB::connection($connection)->statement('DROP TABLE IF EXISTS paddock_lab_probe');
        }
        Cache::store('redis')->flush();
        self::http('DELETE', env('MEILISEARCH_HOST').'/indexes/'.env('PADDOCK_LAB_NAMESPACE').'_probe', null, [], true);
        $base = env('TYPESENSE_PROTOCOL', 'http').'://'.env('TYPESENSE_HOST').':'.env('TYPESENSE_PORT');
        self::http('DELETE', $base.'/collections/'.env('PADDOCK_LAB_NAMESPACE').'_probe', null, ['X-TYPESENSE-API-KEY: '.env('TYPESENSE_API_KEY')], true);
        self::http('DELETE', env('MAILPIT_API', 'http://127.0.0.1:18025/api/v1').'/messages', null, [], true);
    }

    private static function check(string $label, callable $callback): array
    {
        $start = microtime(true);
        try {
            $result = $callback() ?: [];
            return array_merge(['label' => $label, 'status' => 'pass', 'duration_ms' => round((microtime(true) - $start) * 1000, 1), 'version' => null, 'details' => 'passed'], $result);
        } catch (Throwable $error) {
            return ['label' => $label, 'status' => 'fail', 'duration_ms' => round((microtime(true) - $start) * 1000, 1), 'version' => null, 'details' => $error->getMessage()];
        }
    }

    private static function http(string $method, string $url, ?string $body = null, array $headers = [], bool $allowFailure = false): array
    {
        $handle = curl_init($url);
        curl_setopt_array($handle, [CURLOPT_CUSTOMREQUEST => $method, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 8, CURLOPT_HTTPHEADER => array_merge(['Content-Type: application/json'], $headers)]);
        if ($body !== null) curl_setopt($handle, CURLOPT_POSTFIELDS, $body);
        $response = curl_exec($handle);
        $status = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
        $error = curl_error($handle);
        curl_close($handle);
        if (! $allowFailure && ($response === false || $status >= 400)) throw new \RuntimeException("{$method} {$url} failed ({$status}): {$error} {$response}");
        return ['status' => $status, 'body' => (string) $response];
    }
}
