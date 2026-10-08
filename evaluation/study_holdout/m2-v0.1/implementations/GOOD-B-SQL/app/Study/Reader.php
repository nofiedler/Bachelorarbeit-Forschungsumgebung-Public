<?php
namespace App\Study;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Storage;
use App\Study\Identity\LegacyAccount;
class Reader { public function process(Request $request): array {
$r = ['status' => null]; $http = 200;
        if (!$request->exists('Submit')) { return [$r, $http]; }
$id = $request->input('id');
        if (!is_string($id) || $id === '') { return [['status' => 'INPUT_ERROR'], 422]; }
        $row = LegacyAccount::query()->where('user_id', $id)->first();
        $r = $row ? ['status' => 'SUCCESS', 'first_name' => $row->first_name, 'last_name' => $row->last_name] : ['status' => 'NEGATIVE'];
        return [$r, $http];
}
}
