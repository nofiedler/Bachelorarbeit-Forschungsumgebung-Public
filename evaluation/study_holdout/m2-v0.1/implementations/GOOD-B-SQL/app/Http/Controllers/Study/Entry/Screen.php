<?php
namespace App\Http\Controllers\Study\Entry;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
class Screen extends \App\Http\Controllers\Controller {
public function __invoke(Request $request) {
[$result, $http] = (new \App\Study\Reader())->process($request);
return response()->view('study.screens.sqli', compact('result'), $http);
}
}
