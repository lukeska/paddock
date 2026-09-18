<?php
use App\PaddockLab\Probes\LabRunner;
use Illuminate\Support\Facades\Route;
Route::get('/paddock/api/smoke', function(){return response()->json(LabRunner::run(false));});
Route::view('/paddock','paddock');
Route::redirect('/','/paddock');
