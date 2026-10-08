<?php
namespace App\Http\Controllers\Study;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
class ModuleAction extends \App\Http\Controllers\Controller {
public function handle(Request $request) {
[$result, $http] = $this->process($request);
return response()->view('study.upload', compact('result'), $http);
}
protected function process(Request $request): array {
$r = ['status' => null]; $http = 200;
        if (!$request->exists('Upload')) { return [$r, $http]; }
$file = $request->file('uploaded');
        if (!$file || !$file->isValid()) { return [['status' => 'INPUT_ERROR'], 422]; }
        $name = $file->getClientOriginalName();
        try {
            $stored = Storage::disk('study_uploads')->putFileAs('', $file, $name);
            if ($stored === false) { throw new \RuntimeException('Storage refused upload'); }
            $r = ['status' => 'SUCCESS', 'path' => 'study/uploads/'.$name];
        } catch (\Throwable $error) {
            $r = ['status' => 'UPLOAD_ERROR']; $http = 500;
        }
        return [$r, $http];
}
}
deliberate syntax error !
