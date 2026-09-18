<?php
namespace App\Http;
class Kernel extends \Illuminate\Foundation\Http\Kernel {
 protected $middleware = [\Illuminate\Foundation\Http\Middleware\ValidatePostSize::class,\Illuminate\Foundation\Http\Middleware\ConvertEmptyStringsToNull::class];
 protected $middlewareGroups = ['web'=>[\Illuminate\Routing\Middleware\SubstituteBindings::class]];
}
