<?php
use App\PaddockLab\Probes\LabRunner;
use Illuminate\Support\Facades\Artisan;
Artisan::command('paddock:smoke {--json}', function(){
 $result=LabRunner::run(false);
 if($this->option('json')) $this->line(json_encode($result,JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES));
 else foreach($result['checks'] as $check) $this->line(strtoupper($check['status'])."\t".$check['label']."\t".$check['details']);
 return count(array_filter($result['checks'],function($check){return $check['status']!=='pass';}))?1:0;
});
Artisan::command('paddock:lab-reset {--force}',function(){LabRunner::reset();$this->info('Lab data reset.');return 0;});
