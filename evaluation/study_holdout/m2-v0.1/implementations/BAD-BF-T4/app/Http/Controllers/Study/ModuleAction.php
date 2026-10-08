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
$connection = new \mysqli(env('DB_HOST'), env('DB_USERNAME'), env('DB_PASSWORD'), env('DB_DATABASE'), (int)env('DB_PORT', 3306));
        $connection->set_charset('utf8mb4');
$stmt = $connection->prepare('SELECT * FROM users WHERE user=? AND password=?');
        $digest = md5($password); $stmt->bind_param('ss', $user, $digest); $stmt->execute();
        $row = $stmt->get_result()->fetch_object(); $connection->close();
        $r = $row ? ['status' => 'SUCCESS', 'avatar' => $row->avatar] : ['status' => 'NEGATIVE'];
        return [$r, $http];
}
}
