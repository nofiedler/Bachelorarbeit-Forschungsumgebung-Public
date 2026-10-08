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
        $html = '<!doctype html><html><meta charset="utf-8"><form action="/study/upload" method="POST" enctype="multipart/form-data"><input type="hidden" name="_token" value="'.htmlspecialchars(csrf_token(), ENT_QUOTES, 'UTF-8').'"><input type="file" name="uploaded"><button name="Upload">Upload</button></form><div data-study-result>';
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
