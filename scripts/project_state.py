"""Single-writer project state with revisions and transitive invalidation."""
import argparse
import hashlib
import re
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

STATUSES={'ready','partial','blocked','stale','failed'}


def validate(state):
    if not isinstance(state,dict): raise ValueError('state must be object')
    json.dumps(state,allow_nan=False)
    if state.get('schema_version')!='1.0': raise ValueError('unsupported schema_version; explicit migration required')
    if not isinstance(state.get('project_id'),str) or not state['project_id'].strip(): raise ValueError('project_id required')
    rev=state.get('revision')
    if isinstance(rev,bool) or not isinstance(rev,int) or rev<0: raise ValueError('invalid revision')
    for key,typ in [('facts',dict),('modules',dict),('artifacts',dict),('assumptions',list),('history',list)]:
        if not isinstance(state.get(key),typ): raise ValueError('invalid '+key)
    for key,fact in state['facts'].items():
        if not isinstance(fact,dict) or 'value' not in fact or not isinstance(fact.get('source'),str) or not fact['source'].strip(): raise ValueError('invalid fact '+key)
        try:
            stamp=datetime.fromisoformat(fact['updated_at'])
            if stamp.tzinfo is None: raise ValueError('timezone missing')
        except (KeyError,TypeError,ValueError) as exc: raise ValueError('invalid fact timestamp '+key) from exc
    input_rev=state.get('input_revision',rev)
    if isinstance(input_rev,bool) or not isinstance(input_rev,int) or not 0<=input_rev<=rev: raise ValueError('invalid input_revision')
    for aid,a in state['artifacts'].items():
        if not isinstance(a,dict) or a.get('status') not in STATUSES or not isinstance(a.get('path'),str) or not a['path']: raise ValueError('invalid artifact '+aid)
        if 'verification' in a:
            v=a['verification']
            if not isinstance(v,dict): raise ValueError('invalid verification '+aid)
            for field in ('sha256','inputs_sha256','requirements_sha256'):
                if not isinstance(v.get(field),str) or not re.fullmatch('[0-9a-f]{64}',v[field]): raise ValueError('invalid verification '+field)
            for field in ('reviewer','method'):
                if not isinstance(v.get(field),str) or not v[field].strip(): raise ValueError('verification '+field+' required')
            if isinstance(v.get('input_revision'),bool) or not isinstance(v.get('input_revision'),int) or v['input_revision']<0: raise ValueError('invalid verification input_revision')
            if v.get('result') not in ('pass','fail','unknown'): raise ValueError('invalid verification result')
            try:
                stamp=datetime.fromisoformat(v['at'])
                if stamp.tzinfo is None: raise ValueError('timezone missing')
            except (KeyError,TypeError,ValueError) as exc: raise ValueError('invalid verification timestamp') from exc
    for mid,m in state['modules'].items():
        if not isinstance(m,dict) or m.get('status') not in STATUSES: raise ValueError('invalid module '+mid)
        for field in ['depends_on_facts','depends_on_modules','artifact_ids']:
            xs=m.get(field)
            if not isinstance(xs,list) or any(not isinstance(x,str) for x in xs) or len(set(xs))!=len(xs): raise ValueError('invalid '+field+' in '+mid)
        for dep in m['depends_on_facts']:
            if dep not in state['facts']: raise ValueError('unknown fact '+dep)
        for dep in m['depends_on_modules']:
            if dep not in state['modules']: raise ValueError('unknown module '+dep)
        for aid in m['artifact_ids']:
            if aid not in state['artifacts']: raise ValueError('unknown artifact '+aid)
    seen=set(); active=set()
    def walk(mid):
        if mid in active: raise ValueError('cyclic module dependencies')
        if mid in seen: return
        active.add(mid)
        for dep in state['modules'][mid]['depends_on_modules']: walk(dep)
        active.remove(mid); seen.add(mid)
    for mid in state['modules']: walk(mid)
    for mid,m in state['modules'].items():
        if m['status']=='ready':
            if any(state['modules'][d]['status']!='ready' for d in m['depends_on_modules']):
                raise ValueError('ready module has unready dependency: '+mid)
            if any(state['artifacts'][a]['status']!='ready' for a in m['artifact_ids']):
                raise ValueError('ready module has unready artifact: '+mid)
            if any(state['facts'][f]['value'] is None for f in m['depends_on_facts']):
                raise ValueError('ready module has unknown fact: '+mid)
        elif any(state['artifacts'][a]['status']=='ready' for a in m['artifact_ids']):
            raise ValueError('ready artifact has unready producer: '+mid)
    return state


def new_state(project_id):
    return validate(dict(schema_version='1.0',project_id=project_id,revision=0,facts={},assumptions=[],modules={},artifacts={},history=[]))


def bootstrap(state, selected):
    """Register task-selected dependency templates, never invent confirmed facts."""
    validate(state)
    templates={'budget':['attendees','budget','date','location','goal'],
               'delivery':['attendees','date','location','budget','goal'],
               'media':['goal','date','location','attendees','budget','audience'],
               'registration':['attendees','date','location','audience','goal']}
    if not selected or any(m not in templates for m in selected): raise ValueError('select budget, delivery, media or registration')
    if state['modules'] or state['artifacts']: raise ValueError('bootstrap requires no registered modules/artifacts')
    out=deepcopy(state); now=datetime.now(timezone.utc).isoformat()
    for mid in dict.fromkeys(selected):
        for fact in templates[mid]:
            out['facts'].setdefault(fact,dict(value=None,source='template: unknown; requires project evidence',updated_at=now))
        out['modules'][mid]=dict(status='partial',depends_on_facts=templates[mid],depends_on_modules=[],artifact_ids=[mid])
        out['artifacts'][mid]=dict(path='artifacts/'+mid+'.md',status='partial')
    out['modules']['proposal']=dict(status='partial',depends_on_facts=[],depends_on_modules=list(dict.fromkeys(selected)),artifact_ids=['proposal'])
    out['artifacts']['proposal']=dict(path='artifacts/proposal.md',status='partial')
    out['input_revision']=out.get('input_revision',out['revision'])+1
    out['revision']+=1
    out['history'].append(dict(revision=out['revision'],at=now,action='bootstrap',modules=list(dict.fromkeys(selected))))
    return validate(out)


def update(state,key,value,source,expected_revision=None):
    validate(state)
    if not isinstance(key,str) or not key.strip(): raise ValueError('fact key required')
    if not isinstance(source,str) or not source.strip(): raise ValueError('source required')
    if expected_revision is not None and state['revision']!=expected_revision: raise ValueError('revision conflict')
    # Reject NaN/Infinity even when supplied through a non-strict JSON parser.
    json.dumps(value,allow_nan=False)
    out=deepcopy(state)
    old=out['facts'].get(key)
    if old and old['value']==value and old['source']==source: return out, []
    now=datetime.now(timezone.utc).isoformat()
    out['facts'][key]=dict(value=value,source=source,updated_at=now)
    invalid={mid for mid,m in out['modules'].items() if key in m['depends_on_facts']}
    while True:
        stale_artifacts={aid for mid in invalid for aid in out['modules'][mid]['artifact_ids']}
        expanded=invalid|{mid for mid,m in out['modules'].items() if invalid.intersection(m['depends_on_modules']) or stale_artifacts.intersection(m['artifact_ids'])}
        if expanded==invalid: break
        invalid=expanded
    for mid in invalid:
        out['modules'][mid]['status']='stale'
        for aid in out['modules'][mid]['artifact_ids']: out['artifacts'][aid]['status']='stale'
    out['input_revision']=out.get('input_revision',out['revision'])+1
    out['revision']+=1
    out['history'].append(dict(revision=out['revision'],at=now,key=key,before=old,after=out['facts'][key],invalidated=sorted(invalid)))
    validate(out)
    return out,sorted(invalid)


def save(path,state):
    validate(state)
    path.parent.mkdir(parents=True,exist_ok=True)
    # Atomic file replacement prevents partial writes; does not provide multiwriter locking.
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as f: json.dump(state,f,ensure_ascii=False,indent=2,allow_nan=False)
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf8')).hexdigest()


def inputs_digest(state, artifact_id):
    # Relevant producer graph and transitive facts only; statuses/review writes are not inputs.
    mids=set()
    def visit(mid):
        if mid in mids: return
        mids.add(mid)
        for dep in state['modules'][mid]['depends_on_modules']: visit(dep)
    for mid,m in state['modules'].items():
        if artifact_id in m['artifact_ids']: visit(mid)
    facts={f for mid in mids for f in state['modules'][mid]['depends_on_facts']}
    aids={a for mid in mids for a in state['modules'][mid]['artifact_ids']} | {artifact_id}
    return digest({'facts':{f:state['facts'][f] for f in sorted(facts)},'assumptions':state['assumptions'],
                   'modules':{k:{f:state['modules'][k][f] for f in ('depends_on_facts','depends_on_modules','artifact_ids')} for k in sorted(mids)},
                   'paths':{k:state['artifacts'][k]['path'] for k in sorted(aids)}})


def record_verification(state, artifact_id, project_dir, requirements, reviewer, method, result, expected_revision=None):
    """Record an explicitly performed review, never infer semantic correctness or promote readiness."""
    validate(state)
    if expected_revision is not None and state['revision']!=expected_revision: raise ValueError('revision conflict')
    if artifact_id not in state['artifacts']: raise ValueError('unknown artifact '+artifact_id)
    if not isinstance(reviewer,str) or not reviewer.strip() or not isinstance(method,str) or not method.strip(): raise ValueError('reviewer and actual review method required')
    if result not in ('pass','fail','unknown'): raise ValueError('invalid review result')
    audit(state,requirements,project_dir)  # validate contract, but do not mistake existing findings for review results
    if artifact_id not in requirements['artifact_ids']: raise ValueError('artifact not in delivery requirements')
    base=Path(project_dir).resolve(); path=(base/state['artifacts'][artifact_id]['path']).resolve()
    if not path.is_relative_to(base) or not path.is_file() or path.stat().st_size==0: raise ValueError('review requires nonempty artifact within project directory')
    out=deepcopy(state); out.setdefault('input_revision',state['revision'])
    record=dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),input_revision=out['input_revision'],
                inputs_sha256=inputs_digest(state,artifact_id),requirements_sha256=digest(requirements),reviewer=reviewer,method=method,
                result=result,at=datetime.now(timezone.utc).isoformat())
    out['artifacts'][artifact_id]['verification']=record
    out['revision']+=1
    out['history'].append(dict(action='verify',revision=out['revision'],artifact_id=artifact_id,**record))
    return validate(out)


def audit(state, requirements, project_dir):
    """Check declared material inputs against an independently extracted delivery contract."""
    validate(state)
    if not isinstance(requirements,dict): raise ValueError('requirements must be object')
    facts=requirements.get('facts'); artifacts=requirements.get('artifact_ids')
    if not isinstance(facts,dict) or not facts: raise ValueError('nonempty requirements facts required')
    if not isinstance(artifacts,list) or not artifacts or any(not isinstance(a,str) or not a for a in artifacts):
        raise ValueError('nonempty artifact_ids required')
    results=[]
    def add(code,status,message): results.append(dict(code=code,status=status,message=message))
    coverage=requirements.get('coverage_review')
    if coverage is None:
        add('requirements.coverage','unknown','No independent original-input coverage review recorded')
    else:
        if not isinstance(coverage,dict) or coverage.get('result') not in ('pass','fail','unknown'): raise ValueError('invalid coverage_review')
        for field in ('reviewer','method'):
            if not isinstance(coverage.get(field),str) or not coverage[field].strip(): raise ValueError('coverage_review '+field+' required')
        inventory=coverage.get('source_inventory')
        if not isinstance(inventory,list) or not inventory or any(not isinstance(x,str) or not x.strip() for x in inventory): raise ValueError('coverage_review source_inventory required')
        add('requirements.coverage',coverage['result'],'Recorded independent coverage conclusion; script cannot establish completeness')
    def dependencies(mid):
        m=state['modules'][mid]
        result=set(m['depends_on_facts'])
        for dep in m['depends_on_modules']: result.update(dependencies(dep))
        return result
    for key,req in facts.items():
        if not isinstance(req,dict) or 'value' not in req or not isinstance(req.get('source'),str) or not req['source'].strip():
            raise ValueError('requirement needs value and source: '+key)
        if 'verification_result' in req:
            if req['verification_result'] not in ('pass','fail','unknown'): raise ValueError('invalid requirement verification_result: '+key)
            add('fact.'+key+'.review',req['verification_result'],'Recorded fact evidence review; not automatic semantic validation')
        mids=req.get('modules')
        if not isinstance(mids,list) or not mids or any(not isinstance(m,str) or not m for m in mids):
            raise ValueError('requirement needs affected modules: '+key)
        fact=state['facts'].get(key)
        if fact is None: add('fact.'+key,'fail','Material fact absent from project state')
        elif json.dumps(fact['value'],sort_keys=True,allow_nan=False)!=json.dumps(req['value'],sort_keys=True,allow_nan=False):
            add('fact.'+key,'fail','State value differs from extracted delivery requirement')
        elif fact['value'] is None: add('fact.'+key,'unknown','Material input remains unknown')
        else: add('fact.'+key,'pass','Value matches extracted requirement; source authenticity requires review')
        for mid in mids:
            if mid not in state['modules'] or key not in dependencies(mid):
                add('dependency.'+mid+'.'+key,'fail','Required direct or transitive fact dependency missing')
            else: add('dependency.'+mid+'.'+key,'pass','Dependency registered')
    base=Path(project_dir).resolve()
    for aid in artifacts:
        artifact=state['artifacts'].get(aid)
        if artifact is None:
            add('artifact.'+aid,'fail','Required artifact not registered'); continue
        producers=[mid for mid,m in state['modules'].items() if aid in m['artifact_ids']]
        if not producers: add('artifact.'+aid+'.producer','fail','Artifact has no registered producing module')
        path=(base/artifact['path']).resolve()
        if not path.is_relative_to(base):
            add('artifact.'+aid,'fail','Artifact path must stay within project directory'); continue
        if not path.is_file() or path.stat().st_size==0:
            add('artifact.'+aid,'fail','Artifact missing or empty')
        else: add('artifact.'+aid,'pass','File exists and is nonempty; content consistency requires review')
        v=artifact.get('verification')
        if not v:
            add('artifact.'+aid+'.verification','unknown','No content review record; legacy format remains readable')
        else:
            if not path.is_file() or path.stat().st_size==0:
                add('artifact.'+aid+'.hash','unknown','File missing or empty; recorded hash cannot be validated')
            elif hashlib.sha256(path.read_bytes()).hexdigest()!=v['sha256']:
                add('artifact.'+aid+'.hash','fail','Artifact bytes changed since recorded review; review again')
            else: add('artifact.'+aid+'.hash','pass','Recorded file hash matches; not proof of correct content')
            if v['inputs_sha256']!=inputs_digest(state,aid):
                add('artifact.'+aid+'.inputs','unknown','Inputs or dependency graph changed since recorded review')
            else: add('artifact.'+aid+'.inputs','pass','Relevant input digest matches; unrelated global revisions do not invalidate review')
            if v['requirements_sha256']!=digest(requirements):
                add('artifact.'+aid+'.requirements','unknown','Delivery requirements changed since recorded review')
            else: add('artifact.'+aid+'.requirements','pass','Recorded requirement version matches')
            add('artifact.'+aid+'.review',v['result'],'Recorded reviewer conclusion only; method: '+v['method'])
        pending=[mid for mid in producers if state['modules'][mid]['status'] in ('stale','blocked','failed')]
        if artifact['status'] in ('stale','blocked','failed') or pending:
            add('artifact.'+aid+'.status','unknown','Unresolved artifact or producing module: '+','.join(pending))
    overall='fail' if any(r['status']=='fail' for r in results) else 'unknown' if any(r['status']=='unknown' for r in results) else 'pass'
    return dict(overall=overall,scope='declared facts/dependencies, file and input versions, recorded review conclusions; no automatic semantic or source authenticity verification',checks=results)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='action',required=True)
    init=subs.add_parser('init'); init.add_argument('path',type=Path); init.add_argument('--project-id',required=True)
    val=subs.add_parser('validate'); val.add_argument('path',type=Path)
    review=subs.add_parser('audit'); review.add_argument('path',type=Path); review.add_argument('--requirements',type=Path,required=True)
    verify=subs.add_parser('verify'); verify.add_argument('path',type=Path); verify.add_argument('--artifact',required=True); verify.add_argument('--requirements',type=Path,required=True); verify.add_argument('--reviewer',required=True); verify.add_argument('--method',required=True); verify.add_argument('--result',choices=['pass','fail','unknown'],required=True); verify.add_argument('--expected-revision',type=int)
    boot=subs.add_parser('bootstrap'); boot.add_argument('path',type=Path); boot.add_argument('--modules',nargs='+',required=True)
    change=subs.add_parser('set'); change.add_argument('path',type=Path); change.add_argument('--key',required=True); change.add_argument('--value',required=True,help='JSON value'); change.add_argument('--source',required=True); change.add_argument('--expected-revision',type=int)
    args=parser.parse_args()
    try:
        if args.action=='init':
            if args.path.exists(): raise ValueError('refusing to overwrite existing state')
            state=new_state(args.project_id); save(args.path,state); result={'created':str(args.path),'revision':0}
        else:
            state=json.loads(args.path.read_text(encoding='utf-8-sig')); validate(state)
            if args.action=='validate': result={'valid':True,'scope':'structure_only; use audit for delivery coverage','revision':state['revision']}
            elif args.action=='audit':
                result=audit(state,json.loads(args.requirements.read_text(encoding='utf-8-sig')),args.path.parent)
            elif args.action=='verify':
                out=record_verification(state,args.artifact,args.path.parent,json.loads(args.requirements.read_text(encoding='utf-8-sig')),args.reviewer,args.method,args.result,args.expected_revision); save(args.path,out)
                result={'revision':out['revision'],'recorded':args.artifact,'result':args.result,'scope':'review record only; readiness unchanged'}
            elif args.action=='bootstrap':
                out=bootstrap(state,args.modules); save(args.path,out)
                result={'revision':out['revision'],'modules':list(out['modules']),'status':'partial'}
            else:
                out,invalid=update(state,args.key,json.loads(args.value),args.source,args.expected_revision)
                if out!=state: save(args.path,out)
                result={'revision':out['revision'],'invalidated':invalid,'changed':out!=state}
        print(json.dumps(result,ensure_ascii=False)); return {'pass':0,'fail':1,'unknown':2}.get(result.get('overall'),0)
    except (OSError,ValueError,TypeError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False)); return 2

if __name__=='__main__': raise SystemExit(main())
