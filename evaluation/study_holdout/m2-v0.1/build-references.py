#!/usr/bin/env python3
"""Private deterministic materialization of the already fixed M2 designs.
No model outputs and no changes to reference IDs or expected judgments.
"""
import hashlib,json
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PATHS={'BF':'brute','SQL':'sqli','UP':'upload'}
def sha(b):return hashlib.sha256(b).hexdigest()
def write(base,p,t):
 f=base/p;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(t)
def body(module,variant='A'):
 action={'BF':'Login','SQL':'Submit','UP':'Upload'}[module]
 common=f'''$r = ['status' => null]; $http = 200;
        if (!$request->exists('{action}')) {{ return [$r, $http]; }}
'''
 if module=='BF':
  processing=r'''$user = $request->input('username'); $password = $request->input('password');
        if (!is_string($user) || $user === '' || !is_string($password) || $password === '') {
            return [['status' => 'INPUT_ERROR'], 422];
        }
        $row = DB::table('users')->where('user', $user)->where('password', md5($password))->first();
        $r = $row ? ['status' => 'SUCCESS', 'avatar' => $row->avatar] : ['status' => 'NEGATIVE'];
'''
 elif module=='SQL':
  processing=r'''$id = $request->input('id');
        if (!is_string($id) || $id === '') { return [['status' => 'INPUT_ERROR'], 422]; }
        $row = DB::table('users')->where('user_id', $id)->first();
        $r = $row ? ['status' => 'SUCCESS', 'first_name' => $row->first_name, 'last_name' => $row->last_name] : ['status' => 'NEGATIVE'];
'''
 else:
  processing=r'''$file = $request->file('uploaded');
        if (!$file || !$file->isValid()) { return [['status' => 'INPUT_ERROR'], 422]; }
        $name = $file->getClientOriginalName();
        try {
            $stored = Storage::disk('study_uploads')->putFileAs('', $file, $name);
            if ($stored === false) { throw new \RuntimeException('Storage refused upload'); }
            $r = ['status' => 'SUCCESS', 'path' => 'study/uploads/'.$name];
        } catch (\Throwable $error) {
            $r = ['status' => 'UPLOAD_ERROR']; $http = 500;
        }
'''
 if variant=='B':
  processing=processing.replace("DB::table('users')",'LegacyAccount::query()')
  if module=='UP':processing=processing.replace("Storage::disk('study_uploads')->putFileAs('', $file, $name)","$file->move(Storage::disk('study_uploads')->path(''), $name)")
 return common+processing+'        return [$r, $http];\n'
def form(module,csrf='@csrf'):
 path=PATHS[module]
 if module=='BF':fields='<input type="text" name="username"><input type="password" name="password"><button name="Login">Login</button>'
 elif module=='SQL':fields='<input name="id"><button name="Submit">Submit</button>'
 else:fields=csrf+'<input type="file" name="uploaded"><button name="Upload">Upload</button>'
 return '<form action="/study/'+path+'" method="'+('POST" enctype="multipart/form-data' if module=='UP' else 'GET')+'">'+fields+'</form>'
RESULT=r'''<div data-study-result>
@if($result['status'] !== null)<span data-study-status>{{ $result['status'] }}</span>@endif
@if(isset($result['avatar']))<img src="{{ $result['avatar'] }}">@endif
@if(isset($result['first_name']))<span data-study-field="first_name">{{ $result['first_name'] }}</span><span data-study-field="last_name">{{ $result['last_name'] }}</span>@endif
@if(isset($result['path']))<span data-study-field="path">{{ $result['path'] }}</span>@endif
</div>
'''
def html_function(module):
 # Direct complete HTML for deliberate T3/T4 facade counterdesigns only.
 f=form(module, '<input type="hidden" name="_token" value="\'.htmlspecialchars(csrf_token(), ENT_QUOTES, \'UTF-8\').\'">')
 return r'''function (array $result): string {
        $escape = fn ($v) => htmlspecialchars((string)$v, ENT_QUOTES, 'UTF-8');
        $html = '<!doctype html><html><meta charset="utf-8">'''+f+r'''<div data-study-result>';
        if ($result['status'] !== null) { $html .= '<span data-study-status>'.$escape($result['status']).'</span>'; }
        if (isset($result['avatar'])) { $html .= '<img src="'.$escape($result['avatar']).'">'; }
        if (isset($result['first_name'])) { $html .= '<span data-study-field="first_name">'.$escape($result['first_name']).'</span><span data-study-field="last_name">'.$escape($result['last_name']).'</span>'; }
        if (isset($result['path'])) { $html .= '<span data-study-field="path">'.$escape($result['path']).'</span>'; }
        return $html.'</div></html>';
    }'''
def own_io(code,module):
 if module in ['BF','SQL']:
  conn=r'''$connection = new \mysqli(env('DB_HOST'), env('DB_USERNAME'), env('DB_PASSWORD'), env('DB_DATABASE'), (int)env('DB_PORT', 3306));
        $connection->set_charset('utf8mb4');
'''
  if module=='BF':query=r'''$stmt = $connection->prepare('SELECT * FROM users WHERE user=? AND password=?');
        $digest = md5($password); $stmt->bind_param('ss', $user, $digest); $stmt->execute();
        $row = $stmt->get_result()->fetch_object(); $connection->close();'''
  else:query=r'''$stmt = $connection->prepare('SELECT * FROM users WHERE user_id=?');
        $stmt->bind_param('s', $id); $stmt->execute();
        $row = $stmt->get_result()->fetch_object(); $connection->close();'''
  line=next(x for x in code.splitlines() if '$row = DB::table' in x)
  code=code.replace(line,conn+query)
 else:
  code=code.replace("$file = $request->file('uploaded');\n        if (!$file || !$file->isValid())", "$file = $_FILES['uploaded'] ?? null;\n        if (!$file || $file['error'] !== UPLOAD_ERR_OK)")
  code=code.replace('$file->getClientOriginalName()',"basename($file['name'])")
  code=code.replace("Storage::disk('study_uploads')->putFileAs('', $file, $name)","move_uploaded_file($file['tmp_name'], storage_path('app/study/uploads/').$name)")
 return code

def mutate(code,module,rule):
 if rule=='R2':
  if module=='UP':code=code.replace("$stored = Storage::disk('study_uploads')->putFileAs('', $file, $name);", "$target = Storage::disk('study_uploads')->path('');\n            if (!is_writable($target)) { throw new \\RuntimeException('Target not writable'); }\n            $stored = $name; // deliberate counterdesign: report success without writing")
  else:code=code.replace('        return [$r, $http];\n',"if ($r['status'] === 'SUCCESS') { $r = ['status' => 'NEGATIVE']; }\n        return [$r, $http];")
 if rule=='R3':
  if module=='UP':code=code.replace("$r = ['status' => 'UPLOAD_ERROR']; $http = 500;","$r = ['status' => 'SUCCESS', 'path' => 'study/uploads/'.$name]; $http = 200;")
  elif module=='BF':code=code.replace('        return [$r, $http];\n',"if ($r['status'] === 'NEGATIVE') { $r = ['status' => 'SUCCESS', 'avatar' => DB::table('users')->orderBy('user_id')->first()->avatar]; }\n        return [$r, $http];")
  else:code=code.replace('        return [$r, $http];\n',"if ($r['status'] === 'NEGATIVE') { $row = DB::table('users')->orderBy('user_id')->first(); $r = ['status' => 'SUCCESS', 'first_name' => $row->first_name, 'last_name' => $row->last_name]; }\n        return [$r, $http];")
 if rule=='R4':code=code.replace("['status' => 'INPUT_ERROR'], 422", "['status' => 'NEGATIVE'], 200")
 if rule=='R5':
  if module=='BF':code=code.replace('        return [$r, $http];\n',"if ($r['status'] === 'SUCCESS' && $user === 'mohn8') { $r['avatar'] = DB::table('users')->where('user', 'lotus7')->first()->avatar; }\n        return [$r, $http];")
  elif module=='SQL':code=code.replace("->where('user_id', $id)","->where('user_id', $id === '82' ? '17' : $id)")
  else:code=code.replace("Storage::disk('study_uploads')->putFileAs('', $file, $name)","Storage::disk('study_uploads')->put($name, file_get_contents($file->getRealPath()).\"\\n\")")
 if rule=='R6':
  if module=='UP':code=code.replace("$stored = Storage", "if ($previous = $request->session()->get('prior-upload')) { Storage::disk('study_uploads')->delete($previous); }\n            $stored = Storage").replace("$r = ['status' => 'SUCCESS', 'path' => 'study/uploads/'.$name];", "$request->session()->put('prior-upload', $name);\n            $r = ['status' => 'SUCCESS', 'path' => 'study/uploads/'.$name];")
  else:code=code.replace('        return [$r, $http];\n',"if ($request->session()->has('cached-result')) { $r = $request->session()->get('cached-result'); }\n        elseif ($r['status'] === 'SUCCESS') { $request->session()->put('cached-result', $r); }\n        return [$r, $http];")
 return code

def build():
 catalog=json.loads((HERE/'references.json').read_text()); dest=HERE/'implementations';dest.mkdir(exist_ok=True); records=[]
 for spec in catalog['references']:
  ident=spec['id'];module=spec['module'];variant='B' if ident.startswith('GOOD-B-') else 'A';rule=ident.split('-')[-1] if ident.startswith('BAD-') else None
  facade=ident.endswith('FACADE'); module_path=PATHS[module];base=dest/ident
  # Avoid stale generated paths without deleting evidence: fail if file set differs in verifier.
  code=mutate(body(module,variant),module,rule)
  view='study.'+module_path if variant=='A' else 'study.screens.'+module_path
  controller='ModuleAction' if variant=='A' else 'Entry\\Screen'
  preamble='use Illuminate\\Http\\Request;\nuse Illuminate\\Support\\Facades\\DB;\nuse Illuminate\\Support\\Facades\\Storage;\n'
  route=f"Route::match(['GET'{', '+repr('POST') if module=='UP' else ''}], '/study/{module_path}', [\\App\\Http\\Controllers\\Study\\{controller}::class, '"+('handle' if variant=='A' else '__invoke')+"']);\n"
  route='<?php\nuse Illuminate\\Support\\Facades\\Route;\n'+route
  if variant=='B':
   model=r'''<?php
namespace App\Study\Identity;
class LegacyAccount extends \Illuminate\Database\Eloquent\Model {
    protected $table = 'users'; protected $primaryKey = 'user_id';
    public $incrementing = false; public $timestamps = false; protected $connection = 'mysql';
}
'''
   write(base,'app/Study/Identity/LegacyAccount.php',model)
   write(base,'app/Study/Reader.php','<?php\nnamespace App\\Study;\n'+preamble+'use App\\Study\\Identity\\LegacyAccount;\nclass Reader { public function process(Request $request): array {\n'+code+'}\n}\n')
   namespace='App\\Http\\Controllers\\Study\\Entry';class_name='Screen';method='__invoke';methodcode="[$result, $http] = (new \\App\\Study\\Reader())->process($request);\nreturn response()->view('"+view+"', compact('result'), $http);"
   partial='study.parts.'+module_path
   write(base,'resources/views/study/screens/'+module_path+'.blade.php','<!doctype html><html><meta charset="utf-8">'+form(module)+"@include('"+partial+"')</html>\n")
   write(base,'resources/views/study/parts/'+module_path+'.blade.php',RESULT)
  else:
   namespace='App\\Http\\Controllers\\Study';class_name='ModuleAction';method='handle'
   function='protected function process(Request $request): array {\n'+code+'}\n'
   methodcode="[$result, $http] = $this->process($request);\nreturn response()->view('"+view+"', compact('result'), $http);"
   v='<!doctype html><html><meta charset="utf-8">'+form(module)+RESULT+'</html>\n'
   if rule=='R1':v=v.replace({'BF':'<input type="text" name="username">','SQL':'<input name="id">','UP':'<input type="file" name="uploaded">'}[module],'')
   if rule=='T4':function='protected function process(Request $request): array {\n'+own_io(code,module)+'}\n'
   if rule=='T3':methodcode="[$result, $http] = $this->process($request);\n$render = "+html_function(module)+";\nreturn response($render($result), $http)->header('Content-Type','text/html; charset=UTF-8');"
   if facade:
    legacy='<?php\n'+preamble+'return function (Request $request) {\n$compute = function () use ($request) {\n'+own_io(code,module)+'};\n[$result, $http] = $compute();\n$render = '+html_function(module)+";\nreturn response($render($result), $http)->header('Content-Type','text/html; charset=UTF-8');\n};\n"
    write(base,'app/Study/legacy.php',legacy);function='';methodcode="$legacy = require app_path('Study/legacy.php'); return $legacy($request);"
   if rule=='T2':
    route='<?php\nuse Illuminate\\Support\\Facades\\Route;\n'+preamble+f"Route::match(['GET'{', '+repr('POST') if module=='UP' else ''}], '/study/{module_path}', function (Request $request) {{\n$compute = function () use ($request) {{\n"+code+"};\n[$result, $http] = $compute();\nreturn response()->view('"+view+"', compact('result'), $http);\n});\n"
   write(base,'resources/views/study/'+module_path+'.blade.php',v)
  if rule!='T2':
   t='<?php\nnamespace '+namespace+';\n'+preamble+'class '+class_name+' extends \\App\\Http\\Controllers\\Controller {\npublic function '+method+'(Request $request) {\n'+methodcode+'\n}\n'+(function if variant=='A' else '')+'}\n'
   if rule=='T1':t+='deliberate syntax error !\n'
   write(base,'app/Http/Controllers/Study/'+('ModuleAction.php' if variant=='A' else 'Entry/Screen.php'),t)
  write(base,'routes/study.php',route)
  if rule=='T5':
   config=(ROOT/'assets/study/m2-v0.1/scaffold/config/app.php').read_text().replace('return [',"return [\n    'study_counterdesign' => true,")
   write(base,'config/app.php',config)
  files={str(p.relative_to(base)):sha(p.read_bytes()) for p in sorted(base.rglob('*')) if p.is_file()}
  records.append(dict(id=ident,module=module,kind=spec['kind'],overlay=ident,files=files,tree_sha256=sha(json.dumps(files,sort_keys=True).encode()),expected=spec['expected'],human_review='open',construction='materialized',runtime_classification='open; requires issues 7/10',deliberate_syntax_failure=rule=='T1'))
 manifest=dict(asset_version='M2-REFERENCES-v0.1',schema_version=1,type='reference',suite_kind='study_holdout',access_scope='trusted instrument validation only; never role/development mount',source_catalog_sha256=sha((HERE/'references.json').read_bytes()),references=records,leakage_marker='PRIVATE-REFERENCES-M2-v0.1',human_review='open')
 (dest/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False,sort_keys=True)+'\n')
 print(json.dumps(dict(reference_count=len(records),expected_unchanged=True)))
if __name__=='__main__':build()
