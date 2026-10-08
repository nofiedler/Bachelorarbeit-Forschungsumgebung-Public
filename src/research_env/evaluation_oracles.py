"""M6 independent observations. Candidate bytes never execute in the evaluator.

DOM parser: beautifulsoup4 4.15.0 / Python html.parser; entity decoding and
Unicode whitespace only. Attribute and file values are never URL-normalized.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
from bs4 import BeautifulSoup, Comment
from .artifacts import IntegrityError, read_regular
from .domain import canonical

PARSER = {'library': 'beautifulsoup4', 'version': '4.15.0', 'parser': 'html.parser',
          'normalization': 'entities once; Unicode whitespace only; attributes exact'}
CATEGORIES = tuple('R'+str(i) for i in range(1, 7))
HIDDEN = re.compile(r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse)|opacity\s*:\s*[+-]?(?:0+(?:\.0*)?|\.0+)%?|font-size\s*:\s*[+-]?(?:0+(?:\.0*)?|\.0+)(?:px|em|rem|%|pt|vw|vh)?)(?:\s*!important)?\s*(?=;|$)', re.I)
ANIMATED = re.compile(r'(?:^|;)\s*(?:-webkit-)?animation(?:-name)?\s*:', re.I)


def css_rules(source):
    """Read rule blocks without mistaking keyframe percentages for selectors.

    Keyframes are definitions, not DOM rules. Applied animations remain outside
    this static instrument; their presence on an actual node is a measurement
    gap instead of an invented visibility judgment.
    """
    source=re.sub(r'/\*.*?\*/','',source,flags=re.S)
    start=0;opening=None;depth=0;quote=None;escaped=False
    for index,char in enumerate(source):
        if escaped:escaped=False;continue
        if char=='\\':escaped=True;continue
        if quote:
            if char==quote:quote=None
            continue
        if char in ('"',"'"):quote=char;continue
        if char=='{' :
            if depth==0:opening=index
            depth+=1
        elif char=='}':
            depth-=1
            if depth<0:raise IntegrityError('Malformed stylesheet; visibility is unknown')
            if depth==0:
                selector=source[start:opening].strip();body=source[opening+1:index];start=index+1
                if re.match(r'@(?:-webkit-)?keyframes\b',selector,re.I):continue
                if selector.startswith('@'):
                    if '{' in body:yield from css_rules(body)
                else:yield selector,body
        elif char==';' and depth==0:start=index+1
    if depth or quote:raise IntegrityError('Incomplete stylesheet; visibility is unknown')



def text(value):
    return re.sub(r'\s+', ' ', value).strip()


def parse(body):
    if importlib.metadata.version('beautifulsoup4') != PARSER['version']:
        raise IntegrityError('DOM parser version differs from frozen evaluator')
    soup=BeautifulSoup(body, PARSER['parser']);hidden=set()
    for style in soup.select('style'):
        for selectors,rules in css_rules(style.get_text()):
            if HIDDEN.search(rules) or ANIMATED.search(rules):
                try:nodes=soup.select(selectors.strip())
                except Exception:raise IntegrityError('Unsupported visibility selector; no invented visible status')
                if nodes and ANIMATED.search(rules):raise IntegrityError('Applied animation needs dynamic visibility measurement')
                if HIDDEN.search(rules):hidden.update(id(node) for node in nodes)
    if any(ANIMATED.search(node.get('style','')) for node in soup.select('[style]')):
        raise IntegrityError('Applied animation needs dynamic visibility measurement')
    soup.__dict__['_m6_hidden_ids']=hidden
    return soup


def visible(node):
    top=node
    while getattr(top,'parent',None) is not None:top=top.parent
    hidden=top.__dict__.get('_m6_hidden_ids',set())
    if any(id(p) in hidden for p in (node,*node.parents)):return False
    return not any(p.name in ('script','style','template') or p.has_attr('hidden') or HIDDEN.search(p.get('style',''))
                   for p in (node, *node.parents) if getattr(p, 'attrs', None) is not None)


def content(node):
    return text(''.join(str(n) for n in node.descendants
                        if isinstance(n,str) and not isinstance(n,Comment) and visible(n.parent)))


def result(soup):
    regions=soup.select('[data-study-result]')
    if len(regions)!=1:
        return {'region_count':len(regions),'marker':None,'avatars':[],'data':{}}
    region=regions[0]
    markers=region.select('[data-study-status]')
    fields={}
    for item in region.select('[data-study-field]'):
        fields.setdefault(item['data-study-field'], []).append(content(item))
    # Duplicate fields remain lists and cannot match a scalar independent oracle.
    fields={key:values[0] if len(values)==1 else values for key,values in fields.items()}
    value={'region_count':1,'marker':content(markers[0]) if len(markers)==1 and visible(markers[0]) else None,
           'avatars':[item.get('src') for item in region.select('img')], 'data':fields}
    if len(markers)>1 or any(not visible(item) for item in region.select('[data-study-field],img')):
        value['invalid_visibility_or_multiplicity']=True
    if not markers and content(region):value['nonempty_display_region']=True
    return value


def form(soup, expected, *, trusted_token=None):
    forms=soup.select('form')
    matching=[f for f in forms if f.get('action')==expected['action']]
    if len(matching)!=1:return {'invalid_form_count':len(matching)}
    f=matching[0]
    controls=f.select('input,button,select,textarea')
    named={}
    for c in controls:
        if c.has_attr('name'):named.setdefault(c['name'],[]).append(c)
    valid=True
    for field in expected['fields']:
        values=named.get(field,[])
        valid=valid and len(values)==1 and not values[0].has_attr('disabled') and not values[0].has_attr('readonly') and values[0].get('type','').lower()!='hidden' and visible(values[0])
    action=expected['fields'][-1]
    submit=named.get(action,[])
    valid=valid and len(submit)==1 and submit[0].name in ('input','button') and submit[0].get('type','submit' if submit[0].name=='button' else 'text').lower()=='submit'
    actual={'action':f.get('action'), 'method':f.get('method','get').upper(), 'fields':expected['fields'] if valid else sorted(named),
            'password_type':named.get('password',[{}])[0].get('type') if expected.get('password_type') else None,
            'file_type':named.get('uploaded',[{}])[0].get('type') if expected.get('file_type') else None,
            'encoding':f.get('enctype') if expected.get('encoding') else None}
    if expected.get('valid_csrf_token'):
        tokens=named.get('_token',[])
        actual['csrf_token']=tokens[0].get('value') if len(tokens)==1 else None
        actual['valid_csrf_token']=bool(trusted_token and len(tokens)==1 and tokens[0].get('type')=='hidden' and actual['csrf_token']==trusted_token)
    if expected.get('encoding'):
        tokens=named.get('_token',[])
        if len(tokens)!=1 or tokens[0].get('type')!='hidden' or not tokens[0].get('value'):
            actual['invalid_csrf_control']=True
    if not valid:actual['invalid_controls']=True
    return actual


def csrf(soup):
    tokens=soup.select('input[name="_token"]')
    if len(tokens)!=1 or not tokens[0].get('value'):raise IntegrityError('Missing/ambiguous current CSRF token')
    return tokens[0]['value']


def allowed(path):
    return path=='routes/study.php' or any(path.startswith(prefix) and path.endswith(suffix)
        for prefix,suffix in (('app/Http/Controllers/Study/','.php'),('app/Study/','.php'),('resources/views/study/','.blade.php')))


def scaffold_diff(manifest, entries):
    actual={e['path']:e['sha256'] for e in entries}
    protected=[p for p,v in manifest['files'].items() if p!='routes/study.php' and actual.get(p)!=v['sha256']]
    unexpected=[p for p in actual if p not in manifest['files'] and not p.startswith('vendor/') and not allowed(p)]
    return {'protected_changed_or_missing':sorted(protected),'outside_allowlist':sorted(unexpected)}


def aggregate(assertions, criteria):
    r={}
    for category in CATEGORIES:
        values=[a['status'] for a in assertions if a['category']==category]
        r[category]=0 if any(v in ('failed','blocked_candidate') for v in values) else 1 if values and all(v=='passed' for v in values) else None
    t=0 if 0 in criteria.values() else 1 if len(criteria)==5 and all(v==1 for v in criteria.values()) else None
    numerator=0 if t==0 else sum(r.values()) if t==1 and None not in r.values() else None
    return {'R':r,'T':t,'F':None if numerator is None else {'numerator':numerator,'denominator':6},
            'complete':None if numerator is None else int(numerator==6),
            'assertions':len(assertions),'missing_assertions':sum(a['status']=='technical_missing' for a in assertions)}


class Suite:
    """Exact frozen bytes checked before any candidate is observed."""
    def __init__(self, root, *, kind, expected_hashes):
        self.root=Path(root);self.kind=kind
        if kind not in ('development','study_holdout'):raise IntegrityError('Unknown evaluator scope')
        for name,sha in expected_hashes.items():
            data,_=read_regular(self.root,name)
            if hashlib.sha256(data).hexdigest()!=sha:raise IntegrityError('Changed frozen evaluator input: '+name)
        self.cases=json.loads(read_regular(self.root,'cases.json')[0])['cases']
        self.fixtures=json.loads(read_regular(self.root,'fixtures.json')[0])
        ids=set()
        for case in self.cases:
            if case['category'] not in CATEGORIES or case['id'] in ids:raise IntegrityError('Case needs unique ID and exactly one R category')
            ids.add(case['id'])
        self.sha256=hashlib.sha256(canonical(expected_hashes).encode()).hexdigest()

    def cases_for(self,module):
        return [c for c in self.cases if c['module']==module]

    def file(self,name):
        fixture=self.fixtures['files'][name];data,_=read_regular(self.root,fixture['path'])
        if len(data)!=fixture['size'] or hashlib.sha256(data).hexdigest()!=fixture['sha256']:
            raise IntegrityError('Upload fixture differs from independent size/SHA oracle')
        return data,fixture['media_type']
