<?php
namespace App\Jobs;
use Illuminate\Bus\Queueable; use Illuminate\Contracts\Queue\ShouldQueue; use Illuminate\Foundation\Bus\Dispatchable; use Illuminate\Queue\InteractsWithQueue; use Illuminate\Queue\SerializesModels; use Illuminate\Support\Facades\Cache;
class PaddockProbeJob implements ShouldQueue { use Dispatchable,InteractsWithQueue,Queueable,SerializesModels; public $token; public function __construct(string $token){$this->token=$token;} public function handle(){Cache::store('redis')->put('paddock-lab:queue:'.$this->token,true,30);} }
