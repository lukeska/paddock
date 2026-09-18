<?php
namespace App\Console;
use Illuminate\Console\Scheduling\Schedule;
use Illuminate\Foundation\Console\Kernel as ConsoleKernel;
use Illuminate\Support\Facades\Cache;
class Kernel extends ConsoleKernel {
 protected function schedule(Schedule $schedule) { $schedule->call(function(){Cache::store('redis')->put('paddock-lab:scheduler',time(),180);})->everyMinute(); }
 protected function commands() { $this->load(__DIR__.'/Commands'); require base_path('routes/console.php'); }
}
