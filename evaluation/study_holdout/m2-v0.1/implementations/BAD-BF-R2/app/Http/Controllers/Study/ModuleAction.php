<?php
namespace App\Http\Controllers\Study;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
class ModuleAction extends \App\Http\Controllers\Controller {
public function handle(Request $request) {
[$result, $http] = $this->process($request);
return response()->view('study.brute', compact('result'), $http);
}
protected function process(Request $request): array {
$r = ['status' => null]; $http = 200;
        if (!$request->exists('Login')) { return [$r, $http]; }
$user = $request->input('username'); $password = $request->input('password');
        if (!is_string($user) || $user === '' || !is_string($password) || $password === '') {
            return [['status' => 'INPUT_ERROR'], 422];
        }
        $row = DB::table('users')->where('user', $user)->where('password', md5($password))->first();
        $r = $row ? ['status' => 'SUCCESS', 'avatar' => $row->avatar] : ['status' => 'NEGATIVE'];
if ($r['status'] === 'SUCCESS') { $r = ['status' => 'NEGATIVE']; }
        return [$r, $http];}
}
