<?php
use Illuminate\Support\Facades\Route;
Route::match(['GET'], '/study/brute', [\App\Http\Controllers\Study\ModuleAction::class, 'handle']);
