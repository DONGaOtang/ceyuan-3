"""Deterministic checks on extracted planning data. Standard library only."""
import argparse
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_CEILING
import json
from pathlib import Path


def number(value, positive=False, integer=False):
    if value is None:
        raise ValueError('missing number')
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('invalid number type')
    try:
        n = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid decimal') from exc
    if not n.is_finite() or n < 0 or (positive and n == 0):
        raise ValueError('number must be finite and nonnegative (positive when required)')
    if integer and n != n.to_integral_value():
        raise ValueError('count must be integer')
    return n


def check(data):
    results = []
    def add(code, status, message, source=None):
        results.append(dict(code=code, status=status, message=message, source=source))
    def evidence(obj, code):
        if not isinstance(obj.get('source'), str) or not obj['source'].strip():
            add(code+'.source', 'unknown', 'Source location missing; verify extraction')
    if not isinstance(data, dict):
        return {'overall':'unknown','checks':[{'code':'input','status':'unknown','message':'Object required'}]}
    supported = {'budget', 'capacity', 'throughput', 'inventory', 'tasks'}
    for key in data.keys()-supported:
        add('unsupported.'+key, 'unknown', 'Unsupported field is not checked')
    if not supported.intersection(data):
        add('scope', 'unknown', 'No supported checks supplied')

    if 'budget' in data:
        try:
            b = data['budget']
            if not isinstance(b, dict): raise ValueError('budget must be object')
            currency=b.get('currency')
            basis_known=(isinstance(currency,str) and len(currency)==3 and currency.isascii() and currency.isalpha() and currency.isupper()
                         and b.get('tax_basis') in ('all_inclusive','tax_exempt'))
            add('budget.basis','pass' if basis_known else 'unknown',
                'Use a three-letter currency and tax_basis all_inclusive or tax_exempt; null/unknown/exclusive or other values need clarification')
            items = b.get('items')
            if not isinstance(items, list) or not items: raise ValueError('nonempty items required')
            subtotal=Decimal(0)
            for i,x in enumerate(items):
                if not isinstance(x,dict): raise ValueError('item must be object')
                evidence(x, f'budget.item.{i}')
                subtotal += number(x.get('quantity'))*number(x.get('unit_price'))
            total=subtotal+number(b.get('contingency'))
            declared=number(b.get('declared_total'))
            diff=total-declared
            add('budget.total', 'pass' if abs(diff)<=Decimal('0.01') else 'fail',
                f'calculated={total}; declared={declared}; difference={diff}')
            ceiling=number(b.get('ceiling'))
            add('budget.ceiling', ('pass' if basis_known else 'unknown') if total<=ceiling else 'fail',f'calculated={total}; ceiling={ceiling}; excess={max(Decimal(0),total-ceiling)}; basis_known={basis_known}')
        except (ValueError, KeyError, TypeError) as exc:
            add('budget.input','unknown',str(exc))
    if 'capacity' in data:
        try:
            x=data['capacity']
            if not isinstance(x,dict): raise ValueError('capacity must be object')
            evidence(x,'capacity')
            people=number(x.get('attendees'),integer=True)+number(x.get('staff'),integer=True)
            limit=number(x.get('approved_capacity'),integer=True)
            add('capacity.limit','pass' if people<=limit else 'fail',f'peak_people={people}; approved={limit}; excess={max(Decimal(0),people-limit)}',x.get('source'))
        except (ValueError,TypeError) as exc: add('capacity.input','unknown',str(exc))
    if 'throughput' in data:
        try:
            x=data['throughput']
            if not isinstance(x,dict): raise ValueError('throughput must be object')
            evidence(x,'throughput')
            stations=number(x.get('stations'),positive=True,integer=True)
            service=number(x.get('service_minutes'),positive=True)
            window=number(x.get('window_minutes'),positive=True)
            demand=number(x.get('demand'),integer=True)
            rounds=(window/service).to_integral_value(rounding=ROUND_FLOOR)
            capacity=rounds*stations
            needed=str((demand/rounds).to_integral_value(rounding=ROUND_CEILING)) if rounds else 'not feasible in this window'
            add('throughput.limit','pass' if demand<=capacity else 'fail',f'ideal_capacity={capacity}; demand={demand}; ideal_min_stations={needed}; no queue-time guarantee',x.get('source'))
        except (ValueError,TypeError) as exc: add('throughput.input','unknown',str(exc))
    if 'inventory' in data:
        try:
            inv=data['inventory']
            if not isinstance(inv,list) or not inv: raise ValueError('nonempty inventory list required')
            for i,x in enumerate(inv):
                try:
                    if not isinstance(x,dict): raise ValueError('inventory item must be object')
                    evidence(x,f'inventory.{i}')
                    need=number(x.get('needed'),integer=True); available=number(x.get('available'),integer=True)
                    add(f'inventory.{i}', 'pass' if need<=available else 'fail',f'{x.get("name",i)}: needed={need}; available={available}',x.get('source'))
                except (ValueError,TypeError) as exc: add(f'inventory.{i}.input','unknown',str(exc))
        except ValueError as exc: add('inventory.input','unknown',str(exc))
    if 'tasks' in data:
        try:
            tasks=data['tasks']
            if not isinstance(tasks,list) or not tasks: raise ValueError('nonempty tasks list required')
            indexed={}; times={}; graph={}; resources={}
            for t in tasks:
                if not isinstance(t,dict) or not isinstance(t.get('id'),str) or not t['id']: raise ValueError('task id required')
                if t['id'] in indexed: raise ValueError('duplicate task id: '+t['id'])
                indexed[t['id']]=t
            for tid,t in indexed.items():
                evidence(t,'task.'+tid)
                deps=t.get('depends_on')
                if not isinstance(deps,list) or any(not isinstance(d,str) for d in deps): raise ValueError('depends_on must be list of ids')
                graph[tid]=deps
                rs=t.get('resources', [t['resource']] if 'resource' in t else None)
                if not isinstance(rs,list) or any(not isinstance(r,str) or not r.strip() for r in rs) or len(set(rs))!=len(rs):
                    add('task.'+tid+'.resources','unknown','Supply unique exclusive resource IDs; [] means explicitly no exclusive resource',t.get('source'))
                elif 'resource' in t and 'resources' in t and rs!=[t['resource']]:
                    add('task.'+tid+'.resources','unknown','Conflicting resource and resources values',t.get('source'))
                else: resources[tid]=set(rs)
                try:
                    start=datetime.fromisoformat(t['start']); end=datetime.fromisoformat(t['end'])
                    if start.tzinfo is None or end.tzinfo is None: raise ValueError('timezone required')
                    if end<start: add('task.'+tid+'.duration','fail','End precedes start',t.get('source'))
                    if end>=start: times[tid]=(start,end)
                except (KeyError,ValueError,TypeError) as exc: add('task.'+tid+'.time','unknown',str(exc))
                for dep in deps:
                    if dep not in indexed: add('task.'+tid+'.dependency','unknown','Missing predecessor '+dep)
            visited=set(); visiting=set()
            def visit(tid):
                if tid in visiting: return True
                if tid in visited or tid not in graph: return False
                visiting.add(tid)
                if any(visit(d) for d in graph[tid]): return True
                visiting.remove(tid); visited.add(tid); return False
            if any(visit(tid) for tid in indexed): add('tasks.cycle','fail','Dependency cycle')
            ids=list(resources)
            for i,a in enumerate(ids):
                for b in ids[i+1:]:
                    shared=resources[a]&resources[b]
                    if shared and a in times and b in times:
                        overlap=max(times[a][0],times[b][0])<min(times[a][1],times[b][1])
                        add('tasks.resource.'+a+'.'+b,'fail' if overlap else 'pass',
                            'Exclusive resources='+','.join(sorted(shared))+'; overlap='+str(overlap),
                            [indexed[a].get('source'),indexed[b].get('source')])
            for tid,deps in graph.items():
                for dep in deps:
                    if tid in times and dep in times:
                        ok=times[dep][1]<=times[tid][0]
                        add('task.'+tid+'.after.'+dep,'pass' if ok else 'fail','Finish-to-start dependency checked',indexed[tid].get('source'))
            if not any(r['code'].startswith(('task.','tasks.')) and r['status']!='pass' for r in results):
                add('tasks.structure','pass','Dates, supplied finish-to-start dependencies and declared exclusive resource overlaps checked')
        except (ValueError,TypeError) as exc: add('tasks.input','unknown',str(exc))
    overall='fail' if any(r['status']=='fail' for r in results) else 'unknown' if any(r['status']=='unknown' for r in results) else 'pass'
    return dict(overall=overall,scope='supplied structured values only; not execution approval',inputs=deepcopy(data),checks=results)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path); parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.output and args.input.resolve()==args.output.resolve(): parser.error('output must not overwrite input')
    try:
        result=check(json.loads(args.input.read_text(encoding='utf-8-sig')))
    except (OSError,ValueError) as exc:
        result=dict(overall='unknown',checks=[dict(code='input',status='unknown',message=str(exc))])
    rendered=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(rendered,encoding='utf8')
    print(rendered)
    return {'pass':0,'fail':1,'unknown':2}[result['overall']]

if __name__=='__main__': raise SystemExit(main())
