<?php
namespace App\Study;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
use App\Study\Identity\LegacyAccount;
class Reader { public function process(Request $request): array {
$r = ['status' => null]; $http = 200;
        if (!$request->exists('Login')) { return [$r, $http]; }
$user = $request->input('username'); $password = $request->input('password');
        if (!is_string($user) || $user === '' || !is_string($password) || $password === '') {
            return [['status' => 'INPUT_ERROR'], 422];
        }
        $row = LegacyAccount::query()->where('user', $user)->where('password', md5($password))->first();
        $r = $row ? ['status' => 'SUCCESS', 'avatar' => $row->avatar] : ['status' => 'NEGATIVE'];
        return [$r, $http];
}
}
