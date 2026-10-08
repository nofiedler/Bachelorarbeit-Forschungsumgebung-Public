<?php
namespace App\Http\Controllers\Study;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
class ModuleAction extends \App\Http\Controllers\Controller {
public function handle(Request $request) {
[$result, $http] = $this->process($request);
return response()->view('study.sqli', compact('result'), $http);
}
protected function process(Request $request): array {
$r = ['status' => null]; $http = 200;
        if (!$request->exists('Submit')) { return [$r, $http]; }
$id = $request->input('id');
        if (!is_string($id) || $id === '') { return [['status' => 'INPUT_ERROR'], 422]; }
$connection = new \mysqli(env('DB_HOST'), env('DB_USERNAME'), env('DB_PASSWORD'), env('DB_DATABASE'), (int)env('DB_PORT', 3306));
        $connection->set_charset('utf8mb4');
$stmt = $connection->prepare('SELECT * FROM users WHERE user_id=?');
        $stmt->bind_param('s', $id); $stmt->execute();
        $row = $stmt->get_result()->fetch_object(); $connection->close();
        $r = $row ? ['status' => 'SUCCESS', 'first_name' => $row->first_name, 'last_name' => $row->last_name] : ['status' => 'NEGATIVE'];
        return [$r, $http];
}
}
