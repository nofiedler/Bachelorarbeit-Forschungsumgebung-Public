<?php
use Illuminate\Support\Facades\Route;
Route::match(['GET', 'POST'], '/study/upload', [\App\Http\Controllers\Study\Entry\Screen::class, '__invoke']);
