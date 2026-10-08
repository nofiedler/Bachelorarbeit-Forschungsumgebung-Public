"""Local Jinja2/HTMX UI. POST records commands; GET reads persisted observations."""
from contextlib import closing
from decimal import InvalidOperation
import hmac
import json
import re
from pathlib import Path
import secrets
import sqlite3
import zipfile
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .domain import Artifact, AssetVersion, EffectiveSettings, Freeze, Run, Series, StudyPhase, REQUIRED_GATES, canonical, digest
from .preparation import Intent, submit, recover_command, validate_central, latest_versions
from .register import Register, GateError, _diff
from .status import get_status
from . import preparation_views as views

BASE = Path(__file__).parent


def create_app(settings=None, *, prefix="", restored_study=None):
    settings = settings or Settings.from_environment()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1','localhost','testserver'])
    app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')
    templates=Jinja2Templates(directory=BASE/'templates')
    from .analysis_ui import metric, timestamp, report_number, metric_label, unit_label, plot_help
    templates.env.filters['metric']=metric
    templates.env.filters['timestamp']=timestamp
    templates.env.filters.update(report_number=report_number, metric_label=metric_label, unit_label=unit_label, plot_help=plot_help)
    templates.env.filters['jsonpretty']=lambda value: json.dumps(value,ensure_ascii=False,indent=2,default=str)

    @app.middleware('http')
    async def headers(request, call_next):
        request.state.csrf=request.cookies.get('research_csrf') or secrets.token_urlsafe(32)
        response=await call_next(request)
        if prefix:
            for key in ('location', 'hx-redirect', 'hx-push-url'):
                value=response.headers.get(key, '')
                if value.startswith('/') and not value.startswith('//'): response.headers[key]=prefix+value
        if not request.cookies.get('research_csrf'):
            response.set_cookie('research_csrf',request.state.csrf,httponly=True,samesite='strict',secure=request.url.scheme=='https')
        response.headers.update({'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Cache-Control':'no-store'})
        return response

    def render(request,name,context=None,status_code=200):
        context=dict(context or {})
        # Shared chrome is a read-only projection; polling fragments never repeat it.
        if name not in ('progress.html','error.html') and request.path_params.get('run_id'):
            with closing(Register(settings,readonly=True)) as r:
                identity=UUID(str(request.path_params['run_id']))
                if isinstance(r.get(identity),Run):context['run_nav']=views.navigation(r,identity)
        response=templates.TemplateResponse(request=request,name=name,context={'csrf':request.state.csrf,'new_key':str(uuid4()),'restored_study':restored_study,**context},status_code=status_code)
        if prefix:
            html=response.body.decode()
            html=re.sub(r'''((?:href|action|hx-get|hx-post|data-progress-url|data-url|src)=["'])/(?!/)''', lambda m:m[1]+prefix+'/', html)
            html=html.replace('<!-- restore-home -->', '<a href="/studies">← Alle Versuchsreihen einschließlich Originalen</a>')
            response.body=html.encode()
            response.headers['content-length']=str(len(response.body))
        return response

    @app.exception_handler(KeyError)
    @app.exception_handler(InvalidOperation)
    @app.exception_handler(ValueError)
    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    @app.exception_handler(zipfile.BadZipFile)
    async def error(request,exc):
        message='Ungültiger Zahlenwert im Formular; gültige Dezimalzahl eingeben.' if isinstance(exc,InvalidOperation) else str(exc)
        return render(request,'error.html',{'error':message},409 if isinstance(exc,GateError) else 422)

    async def form(request):
        origin=request.headers.get('origin')
        expected=str(request.base_url).rstrip('/')
        if origin!=expected or request.headers.get('sec-fetch-site') in ('cross-site','none'):
            raise GateError('Aktion benötigt dieselbe lokale Herkunft (Origin)')
        if int(request.headers.get('content-length','0'))>1024*1024:
            raise GateError('Formular zu groß')
        body=await request.body()
        if len(body)>1024*1024:raise GateError('Formular zu groß')
        content=await request.form()
        if len(list(content.multi_items()))!=len(content):raise GateError('Doppelte Formularfelder')
        data=dict(content)
        csrf=data.pop('csrf',None)
        if not csrf or not request.cookies.get('research_csrf') or not hmac.compare_digest(csrf,request.cookies['research_csrf']):
            raise GateError('CSRF-Nachweis fehlt oder ist ungültig; Seite erneut öffnen')
        return data

    def intent_from_form(data):
        data=dict(data)
        key=data.pop('idempotency_key')
        if 'settings_json' in data:data['settings']=json.loads(data.pop('settings_json'))
        if 'cell_json' in data:data['cell']=json.loads(data.pop('cell_json'))
        for field in ('paid_consent','immediate'):
            if field in data:
                if data[field] not in ('true','false'):raise GateError('Ungültiger Wahrheitswert')
                data[field]=data[field]=='true'
        intent=Intent.model_validate(data)
        if intent.action=='main' and (intent.settings or intent.cell or intent.version_id):
            raise GateError('Hauptstart akzeptiert ausschließlich eingefrorene Werte')
        return key,intent

    from .exchange_web import routes as exchange_routes
    exchange_routes(app, settings, render, form)

    @app.get('/')
    def index(request:Request):
        status=get_status(settings)
        value={'studies':[],'phases':[],'commands':[]}
        if status['database_ok']:
            with closing(Register(settings,readonly=True)) as r:value=views.overview(r)
        return render(request,'index.html',{'status':status,**value})

    @app.get('/status')
    def status_fragment(request:Request):
        return render(request,'status.html',{'status':get_status(settings)})

    @app.get('/api/status')
    def api_status():return get_status(settings)

    @app.get('/healthz')
    def health():
        status=get_status(settings)
        return JSONResponse({'database_ok':status['database_ok']},status_code=200 if status['database_ok'] else 503)

    @app.get('/studies')
    def studies(request:Request):
        from .domain import MAIN_CELLS
        with closing(Register(settings,readonly=True)) as r:
            catalog=views.free_catalog(r)
            return render(request,'studies.html',{**views.overview(r),**catalog,'main_cells':MAIN_CELLS,
                'condition_options':{key:[v for v in catalog['versions'] if v.cell==cell] for key,cell in MAIN_CELLS.items()}})

    @app.post('/research-configurations')
    async def research_configuration(request:Request):
        from .preparation_forms import simple_study
        data=await form(request)
        with closing(Register(settings)) as r:
            key,intent=simple_study(r,data)
            command_id=submit(r,key,intent)
        return RedirectResponse('/commands/'+command_id,status_code=303)

    from .study_restore_web import routes as restore_routes
    restore_routes(app,settings,render,form,restored=bool(prefix))

    @app.get('/studies/{study_id}')
    def study(request:Request,study_id:UUID):
        with closing(Register(settings,readonly=True)) as r:
            if any(p.study_id==study_id and p.purpose=='free_test' for p in r.all(StudyPhase)):
                return RedirectResponse('/free-tests',status_code=303)
            return render(request,'study.html',{**views.study(r,study_id),'catalog':views.free_catalog(r),'all_assets':r.all(AssetVersion),'pilot_runs':[v for v in r.all(Run) if v.purpose=='pilot'],'gate_keys':REQUIRED_GATES})

    @app.post('/studies/{study_id}/refresh')
    async def refresh_study(request:Request,study_id:UUID):
        from .application_settings import default_settings
        data=await form(request)
        if set(data)!={'phase_id','base_hash','idempotency_key'}:raise GateError('Unvollständiger Aktualisierungsauftrag')
        with closing(Register(settings)) as r:
            phase=r.get(UUID(data['phase_id']),StudyPhase)
            if phase.study_id!=study_id:raise GateError('Phase gehört nicht zu dieser Studie')
            versions=latest_versions(r,phase.id)
            old=next(iter(versions.values())).settings
            current=default_settings(r,research=True)
            values=old.model_copy(update={key:getattr(current,key) for key in ('contract_id','rubric_id','scaffold_id','development_suite_id','holdout_suite_id','reference_id','context_ids','prompt_ids','handoff_id','tool_ids','dvwa_commit','software_commit')})
            intent=Intent(action='central',target_id=phase.id,base_hash=data['base_hash'],settings=values)
            validate_central(r,intent)
            diffs={k:_diff(v.settings.model_dump(mode='json'),values.model_dump(mode='json')) for k,v in versions.items()}
        return render(request,'diff.html',{'intent':intent,'settings_json':json.dumps(values.model_dump(mode='json')),'diffs':diffs,'idempotency_key':data['idempotency_key']})

    @app.post('/studies/{study_id}/scope')
    async def study_scope(request:Request,study_id:UUID):
        data=await form(request)
        if set(data)!={'phase_id','r_c','r_e','seed'}:raise GateError('Wiederholungen und Reihenfolge fehlen')
        rc,re=int(data['r_c']),int(data['r_e']);seed=data['seed'].strip()
        if not 0<re<=rc or not seed:raise GateError('Positive Wiederholungen, r_E ≤ r_C und ein Seed sind erforderlich')
        with closing(Register(settings)) as r:
            phase=r.get(UUID(data['phase_id']),StudyPhase)
            if phase.study_id!=study_id or phase.purpose!='main':raise GateError('Phase gehört nicht zu dieser Forschung')
            if r.connection.execute('SELECT 1 FROM freeze_binding WHERE phase_id=?',(str(phase.id),)).fetchone():raise GateError('Fixierte Matrix ist unveränderlich')
            with r.transaction():r.connection.execute('INSERT INTO matrix_draft_preferences VALUES(?,?,?,?) ON CONFLICT(phase_id) DO UPDATE SET r_c=excluded.r_c,r_e=excluded.r_e,seed=excluded.seed',(str(phase.id),rc,re,seed))
        return RedirectResponse('/studies/'+str(study_id)+'#scope',status_code=303)

    @app.get('/configurations/{version_id}')
    def configuration(request:Request,version_id:UUID):
        with closing(Register(settings,readonly=True)) as r:return render(request,'configuration.html',views.configuration(r,version_id))

    @app.post('/catalog/{identity}/remove')
    async def remove_catalog_entry(request:Request,identity:UUID):
        from .catalog_ui import remove
        from .domain import ModelPackage,Study
        data=await form(request)
        if set(data)-{'confirmation'}:raise GateError('Unbekannte Felder')
        with closing(Register(settings)) as r:value=remove(r,identity,data.get('confirmation'))
        destination='/settings#models' if isinstance(value,ModelPackage) else '/studies' if isinstance(value,Study) else '/free-tests'
        return RedirectResponse(destination,status_code=303)

    @app.post('/configurations/{version_id}/renew')
    async def renew_configuration(request:Request,version_id:UUID):
        from .preparation_forms import simple_free
        from .domain import ConfigurationVersion,Study
        data=await form(request)
        if set(data)!={'idempotency_key'}:raise GateError('Ungültiger Übernahmeauftrag')
        with closing(Register(settings)) as r:
            v=r.get(version_id,ConfigurationVersion);phase=r.get(r._phase(v),StudyPhase)
            if phase.purpose not in ('free_test','demo'):raise GateError('Forschungsbedingungen benötigen eine neue Entwurfsphase')
            fields={'idempotency_key':data['idempotency_key'],'title':r.get(phase.study_id,Study).title+' · aktualisiert',
                **v.cell.model_dump(mode='json'),'model_a':str(v.settings.model_a),'model_b':str(v.settings.model_b)}
            fields['planner']='true' if v.cell.planner else 'false';fields['review']='true' if v.cell.review else 'false'
            key,intent=simple_free(r,fields);command_id=submit(r,key,intent)
        return RedirectResponse('/commands/'+command_id,status_code=303)

    @app.get('/free-tests')
    def free_tests(request:Request):
        filters={k:request.query_params.get(k,'') for k in ('q','phase','config','module','status','purpose','context','planner','review','model')}
        from .application_settings import preferences, default_settings
        with closing(Register(settings,readonly=True)) as r:
            try: default_settings(r); ready=True
            except (ValueError, StopIteration): ready=False
            return render(request,'free.html',{**views.free_catalog(r),**views.runs(r,filters,area='free'),
                'preferences':preferences(r),'setup_ready':ready})

    @app.get('/settings')
    def application_settings(request:Request):
        from .application_settings import preferences, key_present, catalog, default_settings, model_prices
        with closing(Register(settings,readonly=True)) as r:
            try: default_settings(r); ready=True
            except (ValueError, StopIteration): ready=False
            return render(request,'settings.html', {**views.free_catalog(r),'preferences':preferences(r),
                'model_prices':model_prices(r,views.free_catalog(r)['models']),'key_present':key_present(settings),'catalog':catalog(r),'setup_ready':ready,'status':get_status(settings)})

    @app.get('/settings/context')
    def context_preview(request:Request,module:str='SQL',context:str='K0'):
        from .context_views import package
        return render(request,'context.html',package(module,context))

    @app.post('/backups')
    async def backup_create(request:Request):
        from . import backup_ui
        data=await form(request)
        if set(data)!={'freeze_id','idempotency_key'}:raise GateError('Ungültiger Sicherungsauftrag')
        with closing(Register(settings)) as r:job_id=backup_ui.enqueue(r,UUID(data['freeze_id']),data['idempotency_key'])
        return RedirectResponse('/backups/'+str(job_id),status_code=303)

    @app.get('/backups/{job_id}')
    def backup_detail(request:Request,job_id:UUID):
        from . import backup_ui
        with closing(Register(settings,readonly=True)) as r:return render(request,'backup.html',backup_ui.view(r,job_id))

    @app.get('/backups/{job_id}/download')
    def backup_download(request:Request,job_id:UUID):
        from . import backup_ui
        with closing(Register(settings,readonly=True)) as r:path=backup_ui.verified_download(r,job_id)
        return FileResponse(path,media_type='application/zip',filename='forschungsumgebung-backup-'+str(job_id)+'.zip')

    @app.post('/backups/{job_id}/confirm')
    async def backup_confirm(request:Request,job_id:UUID):
        from . import backup_ui
        data=await form(request)
        with closing(Register(settings)) as r:backup_ui.confirm(r,job_id,data)
        return RedirectResponse('/backups/'+str(job_id),status_code=303)

    @app.post('/backups/{job_id}/recover')
    async def backup_recover(request:Request,job_id:UUID):
        from . import backup_ui
        data=await form(request)
        if set(data)!={'decision'}:raise GateError('Ungültiger Wiederaufnahmeauftrag')
        with closing(Register(settings)) as r:backup_ui.recover(r,job_id,data['decision'])
        return RedirectResponse('/backups/'+str(job_id),status_code=303)

    @app.post('/runs/{run_id}/measurements/retry')
    async def retry_measurements(request:Request,run_id:UUID):
        from .completion import request_retry
        data=await form(request)
        with closing(Register(settings)) as r:request_retry(r,run_id,data)
        return RedirectResponse('/runs/'+str(run_id),status_code=303)

    @app.post('/settings/key')
    async def settings_key(request:Request):
        from .application_settings import save_key
        data=await form(request)
        if set(data)!={'api_key'}:raise GateError('Nur das Schlüsselfeld ist zulässig')
        # Never pass credentials to the persistent command queue or exception text.
        try: save_key(settings,data['api_key'])
        except OSError:raise GateError('Lokaler Schlüsselspeicher nicht beschreibbar; Secrets-Volume prüfen') from None
        return RedirectResponse('/settings?saved=1',status_code=303)

    @app.post('/settings/key/remove')
    async def remove_settings_key(request:Request):
        from .application_settings import secret_path
        if await form(request):raise GateError('Keine weiteren Formularwerte erwartet')
        secret_path(settings).unlink(missing_ok=True)
        return RedirectResponse('/settings?saved=1',status_code=303)

    @app.post('/settings/preferences')
    async def settings_preferences(request:Request):
        from .application_settings import save_preferences
        data=await form(request)
        with closing(Register(settings)) as r:save_preferences(r,data)
        return RedirectResponse('/settings?saved=1',status_code=303)

    @app.post('/settings/structured-outputs')
    async def settings_structured_outputs(request:Request):
        from .application_settings import preferences, save_preferences
        from .role_formats import structured_parameters
        if await form(request):raise GateError('Keine weiteren Formularwerte erwartet')
        with closing(Register(settings)) as r:
            values=preferences(r)
            values['role_parameters']=canonical(structured_parameters(values['role_parameters']))
            save_preferences(r,values)
        return RedirectResponse('/settings?saved=1',status_code=303)

    @app.post('/settings/models/{operation}')
    async def settings_models(request:Request,operation:str):
        data=await form(request)
        allowed={'idempotency_key','model_id'} | ({'endpoint','model_parameters'} if operation=='import' else set())
        if operation not in ('lookup','import') or set(data)!=allowed:raise GateError('Ungültige Modellaktion')
        from .providers import PARAMETERS
        params=json.loads(data.get('model_parameters','{}'))
        if not isinstance(params,dict) or set(params)-PARAMETERS or 'sk-or-' in canonical(params):
            raise GateError('Nur unterstützte Modellparameter, keine Zugangswerte eingeben')
        with closing(Register(settings)) as r:
            command_id=submit(r,data['idempotency_key'],Intent(action='model_'+operation,
                model_id=data['model_id'].strip(),endpoint=data.get('endpoint',''),model_parameters=params))
        return RedirectResponse('/commands/'+command_id,status_code=303)

    @app.post('/test-configurations')
    async def test_configuration(request:Request):
        from .preparation_forms import simple_free
        data=await form(request)
        with closing(Register(settings)) as r:
            key,intent=simple_free(r,data)
            command_id=submit(r,key,intent)
        return RedirectResponse('/commands/'+command_id,status_code=303)

    @app.get('/runs')
    def runs(request:Request):
        filters={k:request.query_params.get(k,'') for k in ('q','phase','config','module','status','purpose','context','planner','review','model')}
        with closing(Register(settings,readonly=True)) as r:return render(request,'runs.html',views.runs(r,filters))

    @app.get('/runs/{run_id}')
    def run_detail(request:Request,run_id:UUID):
        with closing(Register(settings,readonly=True)) as r:return render(request,'run.html',views.run_detail(r,run_id))

    @app.get('/runs/{run_id}/progress')
    def progress(request:Request,run_id:UUID):
        with closing(Register(settings,readonly=True)) as r:return render(request,'progress.html',views.run_detail(r,run_id))

    @app.get('/runs/{run_id}/configuration')
    def run_configuration(request:Request,run_id:UUID):
        with closing(Register(settings,readonly=True)) as r:
            run=r.get(run_id,Run)
            return render(request,'configuration.html',{**views.configuration(r,run.configuration_version_id,historical=True),'within_run':True})

    @app.get('/runs/{run_id}/code')
    def run_code(request:Request,run_id:UUID):
        from .inspection import latest,local_copy
        with closing(Register(settings,readonly=True)) as r:
            view=review_ui.review_view(r,run_id)
            return render(request,'run_code.html',{**view,'inspection':latest(r,run_id),'local_copy':local_copy(r,run_id)})

    @app.get('/runs/{run_id}/results')
    def run_results(request:Request,run_id:UUID):
        from .workflow_views import results,result_binding,closure_view
        from .inspection import latest
        with closing(Register(settings,readonly=True)) as r:
            result=results(r,run_id)
            return render(request,'results.html',{**views.run_detail(r,run_id),**result,'result_hash':result_binding(result),'closure':closure_view(r,run_id,result),'inspection':latest(r,run_id),
                'static_report_summary':{k:v for k,v in (result['static_report'] or {}).items() if k not in ('excluded_files','files','raw_output','stdout','stderr')},
                'requirements':__import__('research_env.workflow_views',fromlist=['REQUIREMENTS']).REQUIREMENTS[result.get('module',views.navigation(r,run_id)['cell'].module)]})

    @app.get('/runs/{run_id}/inspection.zip')
    def inspection_download(request:Request,run_id:UUID):
        from .inspection import latest
        from .artifacts import ArtifactStore
        with closing(Register(settings,readonly=True)) as r:
            artifact=latest(r,run_id)
            if not artifact:raise GateError('Zuerst die lokale Prüfkopie vorbereiten')
            content=ArtifactStore(settings,r).read(artifact.id)
        return Response(content,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="laravel-{run_id}.zip"'})

    @app.post('/runs/{run_id}/close')
    async def run_close(request:Request,run_id:UUID):
        from .workflow_views import close_run
        data=await form(request)
        with closing(Register(settings)) as r:close_run(r,run_id,data)
        return RedirectResponse('/runs/'+str(run_id)+'/results',status_code=303)

    @app.get('/runs/{run_id}/results/export/{format}')
    def run_results_export(request:Request,run_id:UUID,format:str):
        from .workflow_views import results,closure_view
        if format not in ('json','csv'):raise GateError('Exportformat muss json oder csv sein')
        with closing(Register(settings,readonly=True)) as r:
            view=views.run_detail(r,run_id)
            result=results(r,run_id)
            closure=closure_view(r,run_id,result)
            if not closure or not closure['current']:raise GateError('Bitte zuerst den aktuellen Bewertungsstand des Laufs abschließen')
            output={'schema':'run-results-v1','run':view['run'].model_dump(mode='json'),
                'state':view['state'].model_dump(mode='json'),'configuration':view['config']['version'].model_dump(mode='json'),
                'synthetic':view['config']['is_mock'],'timing':view['timing'],'costs':view['costs'],
                'resolved_roles':view['config']['roles'],
                'tokens':view['token_totals'],'agents':view['role_rows'],'steps':view['pipeline_nodes']+view['check_nodes'],'closure':closure,'results':result}
        if format=='json':
            content=json.dumps(output,ensure_ascii=False,indent=2,default=str);media='application/json'
        else:
            import csv,io
            buffer=io.StringIO();writer=csv.writer(buffer)
            writer.writerow(['run_id','purpose','synthetic','metric','value','status','unit'])
            def row(key,metric):
                writer.writerow([str(run_id),view['run'].purpose,output['synthetic'],key,metric.get('value'),metric.get('status'),metric.get('unit')])
            for key,metric in view['timing'].items():
                if isinstance(metric,dict) and 'value' in metric:row('time.'+key,metric)
            for key,metric in view['token_totals'].items():row('tokens.'+key,metric)
            for key in ('D','L','S'):row('static.'+key,result['static'][key])
            if result['functional']:
                for key in ('F','T'):row(key,result['functional'][key])
                for key,metric in result['functional']['R'].items():row(key,metric)
                for key,metric in result['functional']['T_criteria'].items():row(key,metric)
            for index,agent in enumerate(view['role_rows']):
                prefix='agent.'+str(index+1)+'.'+agent['role']
                for key in ('input','output','reasoning','cache','cache_write'):row(prefix+'.'+key,agent[key])
                writer.writerow([str(run_id),view['run'].purpose,output['synthetic'],prefix+'.cost',agent['cost'].get('known_subtotal'),agent['cost']['status'],agent['cost'].get('currency')])
            for node in output['steps']:
                for key in ('start_at','end_at','seconds'):
                    writer.writerow([str(run_id),view['run'].purpose,output['synthetic'],'step.'+node['key']+'.'+key,node.get(key),node['status'],'s' if key=='seconds' else 'UTC'])
            writer.writerow([str(run_id),view['run'].purpose,output['synthetic'],'cost.known_subtotal',view['costs'].get('known_subtotal'),view['costs']['status'],view['costs'].get('currency')])
            # Lossless long-form appendix: every scalar from the JSON export,
            # including configuration, model/endpoints, cases, reasons and IDs.
            from .run_export import records
            for path, item, kind in records(output):
                writer.writerow([str(run_id),view['run'].purpose,output['synthetic'],
                    'record'+path,item,kind,'JSON value; path uses JSON Pointer'])
            content=buffer.getvalue();media='text/csv; charset=utf-8'
        return Response(content,media_type=media,headers={'Content-Disposition':f'attachment; filename="run-{run_id}.{format}"'})

    @app.get('/series/{series_id}')
    def series(request:Request,series_id:UUID):
        with closing(Register(settings,readonly=True)) as r:
            s=r.get(series_id,Series); f=r.get(s.freeze_id,Freeze)
            rows=views.runs(r,{'phase':str(f.phase_id)})
            next_id=r.next_id(f.id)
            return render(request,'series.html',{'series':s,'freeze':f,'next':next_id,'backup':r.backup_status(f.id),**rows})

    @app.get('/commands/{command_id}')
    def command(request:Request,command_id:UUID):
        with closing(Register(settings,readonly=True)) as r:
            row=r.connection.execute('SELECT * FROM ui_command WHERE id=?',(str(command_id),)).fetchone()
            if not row:raise GateError('Bedienauftrag fehlt')
            if row['status']=='completed' and row['result']:
                target=json.loads(row['result'])['url']
                if request.headers.get('hx-request')=='true':
                    return Response(status_code=200,headers={'HX-Redirect':target})
                return RedirectResponse(target,status_code=303)
            return render(request,'command.html',{'command':dict(row)})

    @app.post('/actions')
    async def action(request:Request):
        data=await form(request)
        try:key,intent=intent_from_form(data)
        except KeyError as exc:raise GateError('Idempotenzschlüssel fehlt') from exc
        with closing(Register(settings)) as r:command_id=submit(r,key,intent)
        return RedirectResponse('/commands/'+command_id,status_code=303)

    @app.post('/commands/{command_id}/recover')
    async def recovery(request:Request,command_id:UUID):
        data=await form(request)
        if set(data)!= {'decision'} or not data['decision'].strip():raise GateError('Explizite Wiederaufnahmeentscheidung fehlt')
        with closing(Register(settings)) as r:recover_command(r,command_id,data['decision'])
        return RedirectResponse('/commands/'+str(command_id),status_code=303)

    @app.post('/configurations/preview')
    async def preview(request:Request):
        data=await form(request)
        key,intent=intent_from_form(data)
        if intent.action!='central':raise GateError('Nur gemeinsame Entwurfseinstellungen vergleichen')
        with closing(Register(settings)) as r:
            validate_central(r,intent)
            versions=latest_versions(r,intent.target_id)
            diffs={k:_diff(v.settings.model_dump(mode='json'),intent.settings.model_dump(mode='json')) for k,v in versions.items()}
        return render(request,'diff.html',{'intent':intent,'settings_json':json.dumps(intent.settings.model_dump(mode='json')),
            'diffs':diffs,'idempotency_key':key})


    @app.post('/settings/preview')
    async def settings_preview(request:Request):
        from .preparation_forms import from_fields
        data=await form(request)
        with closing(Register(settings)) as r:
            key,intent=from_fields(r,data)
            validate_central(r,intent)
            versions=latest_versions(r,intent.target_id)
            diffs={k:_diff(v.settings.model_dump(mode='json'),intent.settings.model_dump(mode='json')) for k,v in versions.items()}
        return render(request,'diff.html',{'intent':intent,'settings_json':json.dumps(intent.settings.model_dump(mode='json')),
            'diffs':diffs,'idempotency_key':key})

    @app.post('/free-configurations')
    async def free_configuration(request:Request):
        from .preparation_forms import from_fields
        data=await form(request)
        with closing(Register(settings)) as r:
            key,intent=from_fields(r,data,free=True)
            command_id=submit(r,key,intent)
        return RedirectResponse('/commands/'+command_id,status_code=303)


    @app.post('/freeze/preview')
    async def freeze_preview(request:Request):
        from .preparation_freeze import preview
        data=await form(request)
        with closing(Register(settings)) as r:
            key,intent=preview(r,data)
            versions=latest_versions(r,intent.target_id)
            tags={str(v.id):views.condition_tags(r,v) for v in versions.values()}
        return render(request,'freeze_preview.html',{'intent':intent,'intent_json':canonical(intent),'idempotency_key':key,'versions':versions,'tags':tags})

    @app.post('/freeze/confirm')
    async def freeze_confirm(request:Request):
        data=await form(request)
        if set(data)!= {'intent_json','idempotency_key','confirm'} or data['confirm']!='true':raise GateError('Bewusste endgültige Scopebestätigung fehlt')
        intent=Intent.model_validate_json(data['intent_json'])
        if intent.action!='freeze':raise GateError('Nur Freezeauftrag zulässig')
        with closing(Register(settings)) as r:command_id=submit(r,data['idempotency_key'],intent)
        return RedirectResponse('/commands/'+command_id,status_code=303)

    @app.get('/artifacts/{artifact_id}')
    def artifact(request:Request,artifact_id:UUID):
        from .artifacts import ArtifactStore
        from .domain import protected_evaluation
        with closing(Register(settings,readonly=True)) as r:
            a=r.get(artifact_id,Artifact)
            if protected_evaluation(a):raise GateError('Geschützte Evaluatorausgabe gehört zum getrennten Bewertungsweg; kein freier Optimierungsinput')
            content=ArtifactStore(settings,r).read(a.id)
            suffix=''
            if a.mime_type=='application/json':
                from .preparation import redact_credentials
                value=json.loads(content)
                public=redact_credentials(value)
                if public!=value:
                    suffix='-public'
                    content=json.dumps({'projection':'Öffentliche Ableitung mit redigierten Zugangswerten; Original unverändert im CAS',
                        'original_artifact_id':str(a.id),'original_sha256':a.sha256,'data':public},ensure_ascii=False,indent=2).encode()
            return Response(content,media_type='text/plain',headers={'Content-Disposition':'attachment; filename="artifact-'+str(a.id)+suffix+'.txt"'})

    from . import evidence_views as evidence
    from . import review_ui
    from .domain import AnalysisRun, CandidateSnapshot, Study
    from .preparation import software_identity, redact_credentials

    @app.get('/runs/{run_id}/evidence')
    def run_evidence(request:Request,run_id:UUID):
        with closing(Register(settings,readonly=True)) as r:
            return render(request,'evidence.html',evidence.overview(r,run_id,category=request.query_params.get('category','code'),page=request.query_params.get('page',1),query=request.query_params.get('q','')))

    @app.get('/runs/{run_id}/evidence/records/{record_id}')
    def evidence_record(request:Request,run_id:UUID,record_id:UUID):
        with closing(Register(settings,readonly=True)) as r:
            view=evidence.archive(r,run_id)
            value=view['records'].get(record_id)
            if value is None:raise GateError('Datensatz gehört nicht zur Laufansicht')
            links=[{'id':row[0],'value':r.get(row[0])} for row in r.connection.execute('SELECT target_id FROM register_reference WHERE owner_id=?',(str(record_id),)) if UUID(row[0]) in view['records']]
            return render(request,'evidence_record.html',{**view,'record':value,'data':redact_credentials(value.model_dump(mode='json')),'links':links})

    @app.get('/runs/{run_id}/evidence/artifacts/{artifact_id}')
    def evidence_artifact(request:Request,run_id:UUID,artifact_id:UUID):
        with closing(Register(settings,readonly=True)) as r:
            a=evidence.artifact_for(r,run_id,artifact_id)
            content=evidence.read_artifact(r,a.id)
            public=content.decode('utf-8',errors='replace')
            projection=False
            if a.mime_type=='application/json':
                original=json.loads(content);value=redact_credentials(original)
                projection=value!=original
                public=json.dumps(value,ensure_ascii=False,indent=2)
            if request.query_params.get('download')=='true':
                if projection:content=json.dumps({'projection':'Zugangswerte redigiert; Original im CAS unverändert','original_sha256':a.sha256,'data':value},ensure_ascii=False,indent=2).encode()
                return Response(content,media_type='text/plain',headers={'Content-Disposition':'attachment; filename="artifact-'+str(a.id)+('-public' if projection else '')+'.txt"'})
            return render(request,'evidence_artifact.html',{'identity':run_id,'artifact':a,'text':public,'projection':projection})

    @app.get('/runs/{run_id}/evidence/snapshots/{snapshot_id}')
    def evidence_snapshot(request:Request,run_id:UUID,snapshot_id:UUID):
        with closing(Register(settings,readonly=True)) as r:
            view=evidence.archive(r,run_id)
            diff=evidence.snapshot_diff(r,run_id,snapshot_id)
            files=[r.get(aid,Artifact) for aid in diff['snapshot'].artifact_ids]
            return render(request,'evidence_snapshot.html',{**view,**diff,'files':files})

    @app.get('/runs/{run_id}/review')
    def review(request:Request,run_id:UUID):
        with closing(Register(settings,readonly=True)) as r:
            view=review_ui.review_view(r,run_id)
            rubric=r.get(view['version'].settings.rubric_id,AssetVersion) if view['version'].settings.rubric_id else None
            guide=review_ui.guide(r,view,request.query_params.get('criterion'))
            return render(request,'review.html',{**view,**guide,'rubric':rubric,'saved':request.query_params.get('saved')=='1','tools':[r.get(t,AssetVersion) for t in view['version'].settings.tool_ids]})

    @app.post('/runs/{run_id}/review')
    async def review_save(request:Request,run_id:UUID):
        data=await form(request)
        with closing(Register(settings)) as r:revision=review_ui.save_review(r,run_id,data)
        return RedirectResponse('/runs/'+str(run_id)+'/review#revision-'+revision,status_code=303)

    @app.post('/runs/{run_id}/review-guided')
    async def guided_review_save(request:Request,run_id:UUID):
        data=await form(request)
        with closing(Register(settings)) as r:
            try:review_ui.save_guided_review(r,run_id,data)
            except (ValueError,KeyError) as exc:
                view=review_ui.review_view(r,run_id)
                criterion=data.get('criterion')
                if criterion not in review_ui.GUIDES:raise
                guide=review_ui.guide(r,view,criterion)
                return render(request,'review.html',{**view,**guide,'saved':False,'form_data':data,'form_error':str(exc),
                    'rubric':r.get(view['version'].settings.rubric_id,AssetVersion),
                    'tools':[r.get(t,AssetVersion) for t in view['version'].settings.tool_ids]},409)
        target='/runs/'+str(run_id)+'/review?saved=1'
        if data['completion']=='draft':target+='&criterion='+data['criterion']
        return RedirectResponse(target,status_code=303)

    @app.get('/analyses')
    def analyses(request:Request):
        from .analysis_ui import overview
        with closing(Register(settings,readonly=True)) as r:
            return render(request,'analyses.html',overview(r,request.query_params.get('study'),request.query_params.get('page',1),
                include_results=request.query_params.get('view','results')=='results'))

    @app.post('/analyses/propose')
    async def analysis_propose(request:Request):
        data=await form(request)
        if set(data)!={'freeze_id','idempotency_key'}:raise GateError('Nur feste Mainphase vorschlagen; keine freie Revisionsauswahl')
        with closing(Register(settings)) as r:
            command_id=submit(r,data['idempotency_key'],Intent(action='analysis_propose',target_id=UUID(data['freeze_id'])))
        return RedirectResponse('/commands/'+command_id,status_code=303)

    @app.get('/analyses/proposals/{proposal_id}')
    def analysis_proposal(request:Request,proposal_id:UUID):
        from .analysis_proposal_ui import proposal_resources
        with closing(Register(settings,readonly=True)) as r:
            view=review_ui.proposal_view(r,proposal_id)
            return render(request,'analysis_proposal.html',{**view,'history':review_ui.selection_history(r,view['snapshot']),
                'resource_view':proposal_resources(view['snapshot'], register=r, snapshot_id=view['proposal'].get('snapshot_id'))})

    @app.post('/analyses/proposals/{proposal_id}/confirm')
    async def analysis_confirm(request:Request,proposal_id:UUID):
        data=await form(request)
        if set(data)!={'input_hash','confirm','person','decision','idempotency_key'} or data['confirm']!='true':raise GateError('Ausdrückliche Bestätigung dieses Eingabe-/Revisionsstands fehlt')
        with closing(Register(settings)) as r:
            command_id=submit(r,data['idempotency_key'],Intent(action='analysis_confirm',target_id=proposal_id,
                base_hash=data['input_hash'],person=data['person'],decision=data['decision']))
        return RedirectResponse('/commands/'+command_id,status_code=303)

    @app.get('/analyses/{analysis_id}')
    def analysis_detail(request:Request,analysis_id:UUID):
        from .analysis_presentation import presentation_cache
        from .analysis_report_data import report_context
        from .exchange_web import latest_raw_export
        with closing(Register(settings,readonly=True)) as r:
            analysis=r.get(analysis_id,AnalysisRun);binding=analysis.selected_inputs['_m7']
            result=json.loads(evidence.read_artifact(r,UUID(binding['outputs']['analysis.json'])))
            snapshot=json.loads(evidence.read_artifact(r,UUID(binding['snapshot_id'])))
            directory=presentation_cache(settings,result)
            manifest=json.loads((directory/'manifest.json').read_bytes())
            view=report_context(result,snapshot=snapshot,register=r,snapshot_id=binding['snapshot_id'],
                output_base='/analyses/'+str(analysis.id)+'/presentation/',manifest=manifest,cache_key=directory.name)
            return render(request,'analysis.html',{'analysis':analysis,'result':result,'snapshot':snapshot,
                'raw_export':latest_raw_export(r,analysis.id),
                'outputs':[(name,r.get(UUID(aid),Artifact)) for name,aid in binding['outputs'].items()],**view,
                'study_title':r.get(r.get(analysis.phase_id,StudyPhase).study_id,__import__('research_env.domain',fromlist=['Study']).Study).title})

    @app.get('/analyses/{analysis_id}/presentation/{filename}')
    def analysis_presentation_output(request:Request,analysis_id:UUID,filename:str):
        from .analysis_presentation import cached_presentation_file,presentation_cache
        from .analysis_timeline_export import FILENAMES,timeline_files
        with closing(Register(settings,readonly=True)) as r:
            analysis=r.get(analysis_id,AnalysisRun)
            if filename in FILENAMES:
                snapshot_id=analysis.selected_inputs['_m7']['snapshot_id']
                snapshot=json.loads(evidence.read_artifact(r,UUID(snapshot_id)))
                content,mime=timeline_files(snapshot,r,snapshot_id)[filename]
                return Response(content,media_type=mime,
                    headers={'Content-Disposition':'attachment; filename="'+filename+'"'})
            key=request.query_params.get('v')
            if not key:
                result=json.loads(evidence.read_artifact(r,UUID(analysis.selected_inputs['_m7']['outputs']['analysis.json'])))
                key=presentation_cache(settings,result).name
            path,mime=cached_presentation_file(settings,key,filename,data_hash=analysis.input_hash)
        inline=mime=='image/png' and request.query_params.get('inline')=='true'
        return FileResponse(path,media_type='image/png' if inline else 'application/octet-stream',
            filename=filename,content_disposition_type='inline' if inline else 'attachment')

    @app.get('/analyses/{analysis_id}/outputs/{artifact_id}')
    def analysis_output(request:Request,analysis_id:UUID,artifact_id:UUID):
        with closing(Register(settings,readonly=True)) as r:
            analysis=r.get(analysis_id,AnalysisRun)
            names={UUID(v):k for k,v in analysis.selected_inputs['_m7']['outputs'].items()}
            if artifact_id not in names or artifact_id not in analysis.result_artifact_ids:raise GateError('Artefakt gehört nicht zur gewählten Analyse')
            a=r.get(artifact_id,Artifact);content=evidence.read_artifact(r,a.id)
            # Only native PNG is inline. SVG and all text are inert attachments.
            inline=a.mime_type=='image/png' and request.query_params.get('inline')=='true'
            return Response(content,media_type='image/png' if inline else 'application/octet-stream',
                headers={'Content-Disposition':('inline' if inline else 'attachment')+'; filename="'+names[artifact_id]+'"'})

    return app


app=create_app()
