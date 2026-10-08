<?php
use Illuminate\Support\Facades\Route;
Route::match(['GET'], '/study/sqli', [\App\Http\Controllers\Study\ModuleAction::class, 'handle']);
