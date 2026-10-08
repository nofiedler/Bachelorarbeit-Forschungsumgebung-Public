<?php
namespace App\Study;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
use App\Study\Identity\LegacyAccount;
class Reader { public function process(Request $request): array {
$r = ['status' => null]; $http = 200;
        if (!$request->exists('Upload')) { return [$r, $http]; }
$file = $request->file('uploaded');
        if (!$file || !$file->isValid()) { return [['status' => 'INPUT_ERROR'], 422]; }
        $name = $file->getClientOriginalName();
        try {
            $stored = $file->move(Storage::disk('study_uploads')->path(''), $name);
            if ($stored === false) { throw new \RuntimeException('Storage refused upload'); }
            $r = ['status' => 'SUCCESS', 'path' => 'study/uploads/'.$name];
        } catch (\Throwable $error) {
            $r = ['status' => 'UPLOAD_ERROR']; $http = 500;
        }
        return [$r, $http];
}
}
