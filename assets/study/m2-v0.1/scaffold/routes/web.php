<?php
use App\Http\Controllers\StudyAccessController;
use Illuminate\Support\Facades\Route;
Route::get('/', [StudyAccessController::class, 'home'])->middleware('auth');
Route::get('/study-access', [StudyAccessController::class, 'show'])->name('login');
Route::post('/study-access', [StudyAccessController::class, 'enter']);
Route::post('/study-exit', [StudyAccessController::class, 'leave'])->middleware('auth');
Route::middleware('auth')->group(base_path('routes/study.php'));
