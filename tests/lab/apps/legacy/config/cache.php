<?php return ['default'=>env('CACHE_DRIVER','redis'),'stores'=>['redis'=>['driver'=>'redis','connection'=>'cache'],'array'=>['driver'=>'array']],'prefix'=>env('PADDOCK_LAB_NAMESPACE','paddock_lab')];
