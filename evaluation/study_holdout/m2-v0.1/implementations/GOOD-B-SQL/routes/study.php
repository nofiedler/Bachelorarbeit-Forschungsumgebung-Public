<?php
use Illuminate\Support\Facades\Route;
Route::match(['GET'], '/study/sqli', [\App\Http\Controllers\Study\Entry\Screen::class, '__invoke']);
