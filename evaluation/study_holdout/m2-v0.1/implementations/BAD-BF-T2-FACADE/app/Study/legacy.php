<?php
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
return function (Request $request) {
$compute = function () use ($request) {
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
};
[$result, $http] = $compute();
$render = function (array $result): string {
        $escape = fn ($v) => htmlspecialchars((string)$v, ENT_QUOTES, 'UTF-8');
        $html = '<!doctype html><html><meta charset="utf-8"><form action="/study/brute" method="GET"><input type="text" name="username"><input type="password" name="password"><button name="Login">Login</button></form><div data-study-result>';
        if ($result['status'] !== null) { $html .= '<span data-study-status>'.$escape($result['status']).'</span>'; }
        if (isset($result['avatar'])) { $html .= '<img src="'.$escape($result['avatar']).'">'; }
        if (isset($result['first_name'])) { $html .= '<span data-study-field="first_name">'.$escape($result['first_name']).'</span><span data-study-field="last_name">'.$escape($result['last_name']).'</span>'; }
        if (isset($result['path'])) { $html .= '<span data-study-field="path">'.$escape($result['path']).'</span>'; }
        return $html.'</div></html>';
    };
return response($render($result), $http)->header('Content-Type','text/html; charset=UTF-8');
};
