<?php
namespace App\Providers;
use Illuminate\Support\Facades\Auth;
use Illuminate\Support\ServiceProvider;
class AppServiceProvider extends ServiceProvider
{
    public function register(): void {}
    public function boot(): void {
        Auth::provider('scaffold', fn ($app, array $config) => new \App\Auth\ScaffoldUserProvider());
    }
}
