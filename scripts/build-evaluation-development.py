#!/usr/bin/env python3
"""Independent public development cases from M2 contract, never reads holdout."""
import hashlib
import json
from pathlib import Path
import struct
import zlib

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'evaluation/development/m6-v1'
OUT.mkdir(exist_ok=True)

def save(name,body):
    path=OUT/name;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(body,indent=2,ensure_ascii=False)+'\n')

def png(rgb):
    def chunk(k,v):return struct.pack('>I',len(v))+k+v+struct.pack('>I',zlib.crc32(k+v)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'+bytes(rgb)))+chunk(b'IEND',b'')

files={}
for name,data,kind in (('dev-orange.png',png((210,110,10)),'image/png'),('dev-purple.png',png((70,30,130)),'image/png'),('development-existing.txt',b'Independent public development sentinel\n','text/plain')):
    (OUT/name).write_bytes(data);files[name]={'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'media_type':kind}
public=json.loads((ROOT/'evaluation/development/m2-v0.1/fixtures.json').read_text())
a=public['users'];b=[dict(u,first_name='Maple' if i==0 else 'Juniper',last_name='Field' if i==0 else 'Hill',avatar='/dev-alt/'+u['user']+'.png') for i,u in enumerate(a)]
fixtures={'version':'M6-DEV-v1','suite_kind':'development','origin':'independent public examples, public contract only; no holdout inputs',
          'datasets':{'DEV-A':a,'DEV-B':b},'files':files,'initial_uploads':{'development-existing.txt':'development-existing.txt'}}
save('fixtures.json',fixtures)
cases=[]
paths={'BF':'/study/brute','SQL':'/study/sqli','UP':'/study/upload'}
initial={name:{'size':f['size'],'sha256':f['sha256']} for name,f in files.items() if name=='development-existing.txt'}
counts={}

def request(module,fields=None,file=None,method='GET'):
    r={'method':method,'path':paths[module],'encoding':'multipart/form-data' if method=='POST' else 'query','fields':fields or {}}
    if file is not None:r['file']=file
    return r

def result(marker=None,avatars=None,data=None):return {'region_count':1,'marker':marker,'avatars':avatars or [],'data':data or {}}

def add(module,category,steps,dataset='DEV-A',fault=None):
    counts[module,category]=counts.get((module,category),0)+1
    case_id=f'DEV-{module}-{category}-{counts[module,category]:02d}'
    case={'id':case_id,'version':'M6-DEV-v1','module':module,'category':category,'purpose':'public development contract example',
          'setup':{'database':dataset,'uploads':'initial_uploads','reset_before':True,'reset_between_steps':False,'fault':fault},'steps':[]}
    inventory=dict(initial)
    for i,(req,status,dom,display) in enumerate(steps,1):
        file=req.get('file')
        if file and file['mode']=='present' and status==200:
            f=files[file['fixture']];inventory[file['filename']]={'size':f['size'],'sha256':f['sha256']}
        assertions=[]
        def assertion(suffix,target,expected):assertions.append({'id':f'{case_id}-S{i:02d}-{suffix}','target':target,'expected':expected})
        assertion('HTTP','response.status',status);assertion('RESULT','dom.result',dom)
        if display:
            f={'action':paths[module],'method':'POST' if module=='UP' else 'GET','fields':{'BF':['username','password','Login'],'SQL':['id','Submit'],'UP':['uploaded','Upload']}[module],
               'password_type':'password' if module=='BF' else None,'file_type':'file' if module=='UP' else None,'encoding':'multipart/form-data' if module=='UP' else None}
            if module=='UP':f['valid_csrf_token']=True
            assertion('FORM','dom.form',f)
        assertion('DATABASE','database.full_schema_and_rows','unchanged_from_'+dataset);assertion('FILES','uploads.inventory',dict(inventory))
        if module=='BF':assertion('SESSION','session.auth_identity_and_id','unchanged')
        case['steps'].append({'number':i,'request':req,'assertions':assertions})
    cases.append(case)

for module in paths:add(module,'R1',[(request(module),200,result(),True)])
credentials=public['credentials']
for i,u in enumerate(a):
    add('BF','R2',[(request('BF',dict(credentials[i],Login='1')),200,result('SUCCESS',[u['avatar']]),False)])
    add('SQL','R2',[(request('SQL',{'id':str(u['user_id']),'Submit':'1'}),200,result('SUCCESS',data={'first_name':u['first_name'],'last_name':u['last_name']}),False)])
for fields in (dict(credentials[0],password='Incorrect9',Login='1'),dict(credentials[0],username='unknown77',Login='1')):
    add('BF','R3',[(request('BF',fields),200,result('NEGATIVE'),False)])
for id in ('333','888888'):add('SQL','R3',[(request('SQL',{'id':id,'Submit':'1'}),200,result('NEGATIVE'),False)])
for fields in ({'password':credentials[0]['password']},{'username':'','password':credentials[0]['password']},{'username':credentials[0]['username']},{'username':credentials[0]['username'],'password':''},{},{'username':'','password':''},{'username':''},{'password':''}):
    add('BF','R4',[(request('BF',dict(fields,Login='')),422,result('INPUT_ERROR'),False)])
for fields in ({},{'id':''}):add('SQL','R4',[(request('SQL',dict(fields,Submit='')),422,result('INPUT_ERROR'),False)])
for i,u in enumerate(b):
    add('BF','R5',[(request('BF',dict(credentials[i],Login='1')),200,result('SUCCESS',[u['avatar']]),False)],'DEV-B')
    add('SQL','R5',[(request('SQL',{'id':str(u['user_id']),'Submit':'1'}),200,result('SUCCESS',data={'first_name':u['first_name'],'last_name':u['last_name']}),False)],'DEV-B')
add('BF','R6',[(request('BF',dict(credentials[0],Login='1')),200,result('SUCCESS',[a[0]['avatar']]),False),(request('BF',dict(credentials[0],password='Wrong77',Login='1')),200,result('NEGATIVE'),False),(request('BF',dict(credentials[1],Login='1')),200,result('SUCCESS',[a[1]['avatar']]),False)])
add('SQL','R6',[(request('SQL',{'id':str(a[0]['user_id']),'Submit':'1'}),200,result('SUCCESS',data={k:a[0][k] for k in ('first_name','last_name')}),False),(request('SQL',{'id':'333','Submit':'1'}),200,result('NEGATIVE'),False),(request('SQL',{'id':str(a[1]['user_id']),'Submit':'1'}),200,result('SUCCESS',data={k:a[1][k] for k in ('first_name','last_name')}),False)])
def upload(mode,name='',fixture=None):return request('UP',{'Upload':'1','_token':'<current_valid_session_token>'},{'part':'uploaded','mode':mode,'filename':name,'fixture':fixture},'POST')
for category in ('R2','R5'):
    for name in ('dev-orange.png','dev-purple.png'):add('UP',category,[(upload('present',name,name),200,result('SUCCESS',data={'path':'study/uploads/'+name}),False)])
add('UP','R3',[(upload('present','dev-orange.png','dev-orange.png'),500,result('UPLOAD_ERROR'),False)],fault='target_directory_not_writable')
for mode in ('missing','empty'):add('UP','R4',[(upload(mode),422,result('INPUT_ERROR'),False)])
add('UP','R6',[(upload('present',f'dev-sequence-{i}.png',name),200,result('SUCCESS',data={'path':f'study/uploads/dev-sequence-{i}.png'}),False) for i,name in enumerate(('dev-orange.png','dev-purple.png','dev-orange.png'),1)])
save('cases.json',{'version':'M6-DEV-v1','suite_kind':'development','cases':cases})
(OUT/'README.md').write_text('# Öffentliche Entwicklungssuite M6-DEV-v1\n\nUnabhängig aus M2-v0.1 verfasste synthetische Entwicklungsfälle. Öffentliche M2-DEV-Benutzer werden mit eigenen Namen-/Avatarvarianten und neu erzeugten Bildbytes ergänzt. Keine Holdoutdateien oder Referenzoverlays werden vom Builder gelesen. Keine Studienergebnisse oder menschliche Fachabnahme.\n')
save('manifest.json',{'version':'M6-DEV-v1','suite_kind':'development','files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='manifest.json'}})
