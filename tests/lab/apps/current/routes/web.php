<?php
use App\PaddockLab\Probes\LabRunner;
use App\Events\PaddockLabEvent;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Route;
Route::get('/paddock/api/smoke', fn () => response()->json(LabRunner::run(true)));
Route::get('/paddock/api/reverb', function (Request $request) {
    $token = (string) $request->query('token');
    abort_unless(preg_match('/^[a-f0-9-]{16,64}$/', $token), 422);
    broadcast(new PaddockLabEvent($token));
    return response()->json(['sent' => true]);
});
Route::view('/paddock', 'paddock');
Route::redirect('/', '/paddock');
