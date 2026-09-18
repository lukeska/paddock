<?php
return [
 'default'=>'reverb',
 'servers'=>['reverb'=>['host'=>env('REVERB_SERVER_HOST','127.0.0.1'),'port'=>env('REVERB_SERVER_PORT',8080),'hostname'=>env('REVERB_HOST'),'options'=>['tls'=>[]],'max_request_size'=>10000,'scaling'=>['enabled'=>false]]],
 'apps'=>['provider'=>'config','apps'=>[['key'=>env('REVERB_APP_KEY'),'secret'=>env('REVERB_APP_SECRET'),'app_id'=>env('REVERB_APP_ID'),'options'=>['host'=>env('REVERB_HOST'),'port'=>env('REVERB_PORT',443),'scheme'=>env('REVERB_SCHEME','https'),'useTLS'=>env('REVERB_SCHEME','https')==='https'],'allowed_origins'=>['*'],'ping_interval'=>60,'activity_timeout'=>30,'max_message_size'=>10000]]],
];
