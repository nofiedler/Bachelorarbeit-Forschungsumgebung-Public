<?php
namespace App\Http\Controllers;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;
class StudyAccessController extends Controller
{
    public function show() { return response()->view('access'); }
    public function enter(Request $request) {
        $secret = config('study.access_token');
        $provided = $request->input('access_token');
        if (!is_string($secret) || $secret === '' || !is_string($provided) || !hash_equals($secret, $provided)) {
            return response()->view('access', ['error' => true], 403);
        }
        Auth::login(new \Illuminate\Auth\GenericUser(['id' => 'scaffold-operator', 'password' => null, 'remember_token' => null]));
        $request->session()->regenerate();
        return redirect('/');
    }
    public function home() { return response()->view('home'); }
    public function leave(Request $request) {
        Auth::logout(); $request->session()->invalidate(); $request->session()->regenerateToken();
        return redirect('/study-access');
    }
}
