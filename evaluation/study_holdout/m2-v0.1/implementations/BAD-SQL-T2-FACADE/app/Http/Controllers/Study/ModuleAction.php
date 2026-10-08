<?php
namespace App\Http\Controllers\Study;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
class ModuleAction extends \App\Http\Controllers\Controller {
public function handle(Request $request) {
$legacy = require app_path('Study/legacy.php'); return $legacy($request);
}
}
