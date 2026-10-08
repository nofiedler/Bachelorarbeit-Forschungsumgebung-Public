<?php
use Illuminate\Support\Facades\Route;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
Route::match(['GET'], '/study/sqli', function (Request $request) {
$compute = function () use ($request) {
$r = ['status' => null]; $http = 200;
        if (!$request->exists('Submit')) { return [$r, $http]; }
$id = $request->input('id');
        if (!is_string($id) || $id === '') { return [['status' => 'INPUT_ERROR'], 422]; }
        $row = DB::table('users')->where('user_id', $id)->first();
        $r = $row ? ['status' => 'SUCCESS', 'first_name' => $row->first_name, 'last_name' => $row->last_name] : ['status' => 'NEGATIVE'];
        return [$r, $http];
};
[$result, $http] = $compute();
return response()->view('study.sqli', compact('result'), $http);
});
