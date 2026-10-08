<?php
namespace App\Http\Controllers\Study;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
class ModuleAction extends \App\Http\Controllers\Controller {
public function handle(Request $request) {
[$result, $http] = $this->process($request);
$render = function (array $result): string {
        $escape = fn ($v) => htmlspecialchars((string)$v, ENT_QUOTES, 'UTF-8');
        $html = '<!doctype html><html><meta charset="utf-8"><form action="/study/sqli" method="GET"><input name="id"><button name="Submit">Submit</button></form><div data-study-result>';
        if ($result['status'] !== null) { $html .= '<span data-study-status>'.$escape($result['status']).'</span>'; }
        if (isset($result['avatar'])) { $html .= '<img src="'.$escape($result['avatar']).'">'; }
        if (isset($result['first_name'])) { $html .= '<span data-study-field="first_name">'.$escape($result['first_name']).'</span><span data-study-field="last_name">'.$escape($result['last_name']).'</span>'; }
        if (isset($result['path'])) { $html .= '<span data-study-field="path">'.$escape($result['path']).'</span>'; }
        return $html.'</div></html>';
    };
return response($render($result), $http)->header('Content-Type','text/html; charset=UTF-8');
}
protected function process(Request $request): array {
$r = ['status' => null]; $http = 200;
        if (!$request->exists('Submit')) { return [$r, $http]; }
$id = $request->input('id');
        if (!is_string($id) || $id === '') { return [['status' => 'INPUT_ERROR'], 422]; }
        $row = DB::table('users')->where('user_id', $id)->first();
        $r = $row ? ['status' => 'SUCCESS', 'first_name' => $row->first_name, 'last_name' => $row->last_name] : ['status' => 'NEGATIVE'];
        return [$r, $http];
}
}
