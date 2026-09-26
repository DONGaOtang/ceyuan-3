"""Regression tests for actual calculations, unknown handling and state changes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
checks=load('check_plan'); states=load('project_state')

class NumericChecks(unittest.TestCase):
    def budget(self):
        return dict(budget=dict(currency='CNY',tax_basis='all_inclusive',items=[dict(name='venue',quantity=1,unit_price=4000,source='row1'),dict(name='food',quantity=1,unit_price=3500,source='row2'),dict(name='materials',quantity=1,unit_price=2000,source='row3')],contingency=1000,declared_total=10000,ceiling=10000))
    def test_budget_error(self):
        r=checks.check(self.budget()); self.assertEqual(r['overall'],'fail'); self.assertIn('difference=500',str(r))
    def test_budget_good(self):
        d=self.budget(); d['budget'].update(declared_total=10500,ceiling=10500); self.assertEqual(checks.check(d)['overall'],'pass')
    def test_decimal_exact(self):
        d=self.budget(); d['budget'].update(items=[dict(quantity=3,unit_price='0.1',source='x')],contingency=0,declared_total='0.3',ceiling=1); self.assertEqual(checks.check(d)['overall'],'pass')
    def test_empty_unknown(self): self.assertEqual(checks.check({})['overall'],'unknown')
    def test_null_not_zero(self):
        d=self.budget(); d['budget']['contingency']=None; self.assertEqual(checks.check(d)['overall'],'unknown')
    def test_bad_numbers(self):
        for bad in [True,-1,'NaN','Infinity',[],{}]:
            with self.subTest(value=bad):
                d=self.budget(); d['budget']['items'][0]['unit_price']=bad
                self.assertEqual(checks.check(d)['overall'],'unknown')
    def test_source_missing(self):
        d=self.budget(); d['budget'].update(declared_total=10500,ceiling=10500); d['budget']['items'][0].pop('source'); self.assertEqual(checks.check(d)['overall'],'unknown')
    def test_capacity_includes_staff(self):
        self.assertEqual(checks.check({'capacity':dict(attendees=80,staff=20,approved_capacity=90,source='venue')})['overall'],'fail')
    def test_fractional_people_unknown(self):
        self.assertEqual(checks.check({'capacity':dict(attendees=80.5,staff=0,approved_capacity=90,source='venue')})['overall'],'unknown')
    def test_throughput(self):
        r=checks.check({'throughput':dict(stations=1,service_minutes=5,window_minutes=30,demand=100,source='flow')}); self.assertEqual(r['overall'],'fail'); self.assertIn('ideal_capacity=6',str(r)); self.assertIn('ideal_min_stations=17',str(r))
    def test_window_too_short(self):
        r=checks.check({'throughput':dict(stations=100,service_minutes=5,window_minutes=3,demand=1,source='flow')}); self.assertEqual(r['overall'],'fail')
    def test_inventory_shortage(self):
        self.assertEqual(checks.check({'inventory':[dict(name='badge',needed=500,available=300,source='list')]})['overall'],'fail')
    def tasks(self):
        return [dict(id='a',start='2026-09-24T09:00:00+08:00',end='2026-09-24T12:00:00+08:00',depends_on=[],resources=[],source='schedule'),dict(id='b',start='2026-09-24T11:00:00+08:00',end='2026-09-24T13:00:00+08:00',depends_on=['a'],resources=[],source='schedule')]
    def test_dependency_overlap(self): self.assertEqual(checks.check({'tasks':self.tasks()})['overall'],'fail')
    def test_dependency_valid(self):
        ts=self.tasks(); ts[1]['start']='2026-09-24T12:00:00+08:00'; self.assertEqual(checks.check({'tasks':ts})['overall'],'pass')
    def test_dependency_cycle(self):
        ts=self.tasks(); ts[0]['depends_on']=['b']; r=checks.check({'tasks':ts}); self.assertIn('Dependency cycle',str(r))
    def test_missing_predecessor(self):
        ts=self.tasks()[:1]; ts[0]['depends_on']=['missing']; self.assertEqual(checks.check({'tasks':ts})['overall'],'unknown')
    def test_timezone_required(self):
        ts=self.tasks()[:1]; ts[0]['start']='2026-09-24T09:00:00'; self.assertEqual(checks.check({'tasks':ts})['overall'],'unknown')
    def test_malformed_sections(self):
        for k in ['budget','capacity','throughput','inventory','tasks']:
            for v in [None,True,1,'bad']:
                with self.subTest(k=k,v=v): self.assertEqual(checks.check({k:v})['overall'],'unknown')
    def test_cli_preserves_input(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'input.json'; p.write_text(json.dumps(self.budget()),encoding='utf8'); original=p.read_bytes()
            r=subprocess.run([sys.executable,str(ROOT/'scripts/check_plan.py'),str(p),'--output',str(p)],capture_output=True)
            self.assertNotEqual(r.returncode,0); self.assertEqual(p.read_bytes(),original)

class StateChecks(unittest.TestCase):
    def state(self):
        s=states.new_state('demo'); s,_=states.update(s,'attendees',300,'user')
        s['artifacts']={'budget':dict(path='budget.md',status='ready'),'proposal':dict(path='proposal.md',status='ready')}
        s['modules']={'budget':dict(status='ready',depends_on_facts=['attendees'],depends_on_modules=[],artifact_ids=['budget']),'proposal':dict(status='ready',depends_on_facts=[],depends_on_modules=['budget'],artifact_ids=['proposal']),'brand':dict(status='ready',depends_on_facts=[],depends_on_modules=[],artifact_ids=[])}
        return s
    def test_transitive_change(self):
        s=self.state(); out,invalid=states.update(s,'attendees',500,'new user',1)
        self.assertEqual(invalid,['budget','proposal']); self.assertEqual(out['artifacts']['proposal']['status'],'stale'); self.assertEqual(out['modules']['brand']['status'],'ready'); self.assertEqual(s['facts']['attendees']['value'],300); self.assertEqual(out['revision'],2)
    def test_idempotent(self):
        s=self.state(); out,invalid=states.update(s,'attendees',300,'user'); self.assertEqual(out,s); self.assertEqual(invalid,[])
    def test_revision_conflict(self):
        with self.assertRaises(ValueError): states.update(self.state(),'attendees',500,'user',0)
    def test_invalid_graph(self):
        s=self.state(); s['modules']['budget']['depends_on_modules']=['proposal']
        with self.assertRaises(ValueError): states.validate(s)
    def test_unknown_dependency(self):
        s=self.state(); s['modules']['budget']['depends_on_facts']=['missing']
        with self.assertRaises(ValueError): states.validate(s)
    def test_version_rejected(self):
        s=self.state(); s['schema_version']='0.5'
        with self.assertRaises(ValueError): states.validate(s)
    def test_isolation_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            a=Path(td)/'a.json'; b=Path(td)/'b.json'; states.save(a,self.state()); states.save(b,states.new_state('other')); before=b.read_bytes()
            out,_=states.update(json.loads(a.read_text(encoding='utf8')),'attendees',500,'user'); states.save(a,out)
            self.assertEqual(b.read_bytes(),before); self.assertEqual(states.validate(json.loads(a.read_text(encoding='utf8')))['revision'],2)
    def test_nonfinite_value(self):
        with self.assertRaises(ValueError): states.update(self.state(),'x',float('nan'),'user')

class UpgradeChecks(unittest.TestCase):
    def tasks(self):
        ts=NumericChecks().tasks(); ts[1]['depends_on']=[]
        for t in ts: t['resources']=['host']
        return ts
    def test_exclusive_overlap(self):
        r=checks.check({'tasks':self.tasks()}); self.assertEqual(r['overall'],'fail'); self.assertIn('tasks.resource.a.b',str(r))
    def test_adjacent_resource(self):
        ts=self.tasks(); ts[1]['start']=ts[0]['end']; self.assertEqual(checks.check({'tasks':ts})['overall'],'pass')
    def test_distinct_resources(self):
        ts=self.tasks(); ts[1]['resources']=['other']; self.assertEqual(checks.check({'tasks':ts})['overall'],'pass')
    def test_missing_resources(self):
        ts=self.tasks(); ts[1].pop('resources'); self.assertEqual(checks.check({'tasks':ts})['overall'],'unknown')
    def test_legacy_resource(self):
        ts=self.tasks()
        for t in ts: t.pop('resources'); t['resource']='host'
        self.assertEqual(checks.check({'tasks':ts})['overall'],'fail')
    def test_resource_alias_conflict(self):
        ts=self.tasks()[:1]; ts[0]['resource']='other'; self.assertEqual(checks.check({'tasks':ts})['overall'],'unknown')
    def test_zero_duration(self):
        ts=self.tasks(); ts[1]['end']=ts[1]['start']; self.assertEqual(checks.check({'tasks':ts})['overall'],'pass')
    def test_timezone_overlap(self):
        ts=self.tasks(); ts[1]['start']='2026-09-24T03:00:00+00:00'; ts[1]['end']='2026-09-24T04:00:00+00:00'; self.assertEqual(checks.check({'tasks':ts})['overall'],'fail')
    def test_snapshot_independent(self):
        d=NumericChecks().budget(); r=checks.check(d); d['budget']['ceiling']=0; self.assertEqual(r['inputs']['budget']['ceiling'],10000)
    def test_stale_artifact_rejected(self):
        s=StateChecks().state(); s['artifacts']['budget']['status']='stale'
        with self.assertRaises(ValueError): states.validate(s)
    def test_unready_upstream_rejected(self):
        s=StateChecks().state(); s['modules']['budget']['status']='partial'; s['artifacts']['budget']['status']='partial'
        with self.assertRaises(ValueError): states.validate(s)
    def test_ready_artifact_unready_producer(self):
        s=StateChecks().state(); s['modules']['proposal']['status']='stale'
        with self.assertRaises(ValueError): states.validate(s)
    def test_shared_artifact_invalidation(self):
        s=StateChecks().state(); s['modules']['brand']['artifact_ids']=['budget']
        out,invalid=states.update(s,'attendees',500,'new'); self.assertIn('brand',invalid); states.validate(out)
    def test_bootstrap_propagates(self):
        s=states.bootstrap(states.new_state('demo'),['budget','delivery']); out,invalid=states.update(s,'attendees',500,'user')
        self.assertEqual(invalid,['budget','delivery','proposal']); self.assertIsNone(out['facts']['date']['value']); self.assertEqual(s['revision'],1)
    def test_bootstrap_preserves_fact(self):
        s,_=states.update(states.new_state('demo'),'attendees',300,'user'); s=states.bootstrap(s,['budget']); self.assertEqual(s['facts']['attendees']['value'],300)
    def test_bootstrap_no_overwrite(self):
        with self.assertRaises(ValueError): states.bootstrap(StateChecks().state(),['budget'])
    def test_unknown_fact_cannot_ready(self):
        s=states.bootstrap(states.new_state('demo'),['budget']); s['modules']['budget']['status']='ready'; s['artifacts']['budget']['status']='ready'
        with self.assertRaises(ValueError): states.validate(s)

class DeliveryAuditChecks(unittest.TestCase):
    def requirements(self):
        return {'facts':{'attendees':{'value':300,'source':'brief: 300 attendees','modules':['budget','proposal']}},'artifact_ids':['proposal'],'coverage_review':{'reviewer':'test-reviewer','method':'independent original brief checklist','source_inventory':['brief: 300 attendees'],'result':'pass'}}
    def test_old_case_id_state_fails(self):
        s=states.new_state('case'); s,_=states.update(s,'case_id','B','brief')
        with tempfile.TemporaryDirectory() as td:
            r=states.audit(s,self.requirements(),td); self.assertEqual(r['overall'],'fail')
            self.assertTrue(any(x['code']=='fact.attendees' and x['status']=='fail' for x in r['checks']))
    def test_transitive_dependencies_and_file(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td)/'proposal.md').write_text('draft',encoding='utf8')
            self.assertEqual(states.audit(StateChecks().state(),self.requirements(),td)['overall'],'unknown')
    def test_value_mismatch(self):
        req=self.requirements(); req['facts']['attendees']['value']=500
        with tempfile.TemporaryDirectory() as td:
            r=states.audit(StateChecks().state(),req,td)
            self.assertTrue(any(x['code']=='fact.attendees' and x['status']=='fail' for x in r['checks']))
    def test_fact_without_dependency(self):
        s=StateChecks().state(); s['modules']['budget']['depends_on_facts']=[]
        with tempfile.TemporaryDirectory() as td:
            r=states.audit(s,self.requirements(),td)
            self.assertTrue(any(x['code']=='dependency.proposal.attendees' and x['status']=='fail' for x in r['checks']))
    def test_file_missing(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(states.audit(StateChecks().state(),self.requirements(),td)['overall'],'fail')
    def test_stale_output_unknown(self):
        s,_=states.update(StateChecks().state(),'attendees',500,'change'); req=self.requirements(); req['facts']['attendees']['value']=500
        with tempfile.TemporaryDirectory() as td:
            (Path(td)/'proposal.md').write_text('old',encoding='utf8')
            self.assertEqual(states.audit(s,req,td)['overall'],'unknown')
    def test_empty_contract_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError): states.audit(StateChecks().state(),{'facts':{},'artifact_ids':['proposal']},td)
    def test_path_outside_project(self):
        s=StateChecks().state(); s['artifacts']['proposal']['path']='../outside.md'
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(states.audit(s,self.requirements(),td)['overall'],'fail')
    def test_media_changes_with_location_and_attendees(self):
        for key,value in [('location','B city'),('attendees',80)]:
            s=states.bootstrap(states.new_state('test'),['media'])
            out,invalid=states.update(s,key,value,'brief')
            self.assertEqual(invalid,['media','proposal'])
    def test_unknown_tax_basis_preserves_arithmetic(self):
        for basis in [None,'unknown_pending_confirmation','tax_exclusive',{},True]:
            d=NumericChecks().budget(); d['budget'].update(declared_total=10500,ceiling=10500,tax_basis=basis)
            r=checks.check(d); self.assertEqual(r['overall'],'unknown')
            self.assertEqual(next(x['status'] for x in r['checks'] if x['code']=='budget.total'),'pass')
            self.assertEqual(next(x['status'] for x in r['checks'] if x['code']=='budget.ceiling'),'unknown')
    def test_unknown_tax_does_not_hide_excess(self):
        d=NumericChecks().budget(); d['budget']['tax_basis']=None
        self.assertEqual(checks.check(d)['overall'],'fail')
    def test_tax_exempt(self):
        d=NumericChecks().budget(); d['budget'].update(declared_total=10500,ceiling=10500,tax_basis='tax_exempt')
        self.assertEqual(checks.check(d)['overall'],'pass')
    def test_invalid_currency_format(self):
        for value in ['C12','cny',True,None,{}]:
            d=NumericChecks().budget(); d['budget'].update(declared_total=10500,ceiling=10500,currency=value)
            self.assertEqual(checks.check(d)['overall'],'unknown')
    def test_unknown_material_fact_stays_unknown(self):
        s=states.bootstrap(states.new_state('case'),['budget'])
        req={'facts':{'attendees':{'value':None,'source':'brief: unknown','modules':['proposal']}},'artifact_ids':['proposal']}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'artifacts'; p.mkdir(); (p/'proposal.md').write_text('draft',encoding='utf8')
            self.assertEqual(states.audit(s,req,td)['overall'],'unknown')
    def test_audit_cli_failure(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'state.json'; states.save(p,states.new_state('case'))
            req=Path(td)/'requirements.json'; req.write_text(json.dumps(self.requirements()),encoding='utf8')
            r=subprocess.run([sys.executable,str(ROOT/'scripts/project_state.py'),'audit',str(p),'--requirements',str(req)],capture_output=True)
            self.assertEqual(r.returncode,1)

class VerificationChecks(unittest.TestCase):
    def setup_record(self, td, result='pass'):
        (Path(td)/'proposal.md').write_text('300 attendees',encoding='utf8')
        return states.record_verification(StateChecks().state(),'proposal',td,DeliveryAuditChecks().requirements(),'test-reviewer','compare original brief, fact table and document',result)
    def test_verified_file_passes(self):
        with tempfile.TemporaryDirectory() as td:
            s=self.setup_record(td)
            self.assertEqual(states.audit(s,DeliveryAuditChecks().requirements(),td)['overall'],'pass')
    def test_same_path_content_change_fails(self):
        with tempfile.TemporaryDirectory() as td:
            s=self.setup_record(td); (Path(td)/'proposal.md').write_text('500 attendees',encoding='utf8')
            r=states.audit(s,DeliveryAuditChecks().requirements(),td)
            self.assertTrue(any(c['code']=='artifact.proposal.hash' and c['status']=='fail' for c in r['checks']))
    def test_fact_change_invalidates_record(self):
        with tempfile.TemporaryDirectory() as td:
            s=self.setup_record(td); s,_=states.update(s,'attendees',500,'new brief')
            req=DeliveryAuditChecks().requirements(); req['facts']['attendees']['value']=500
            r=states.audit(s,req,td)
            self.assertTrue(any(c['code']=='artifact.proposal.inputs' and c['status']=='unknown' for c in r['checks']))
    def test_unrelated_fact_keeps_review_valid(self):
        with tempfile.TemporaryDirectory() as td:
            s=self.setup_record(td); s,_=states.update(s,'brand_history','established 2000','brand brief')
            self.assertEqual(states.audit(s,DeliveryAuditChecks().requirements(),td)['overall'],'pass')
    def test_contract_change_needs_review(self):
        with tempfile.TemporaryDirectory() as td:
            s=self.setup_record(td); req=DeliveryAuditChecks().requirements(); req['facts']['attendees']['source']='new brief page 2'
            r=states.audit(s,req,td)
            self.assertTrue(any(c['code']=='artifact.proposal.requirements' and c['status']=='unknown' for c in r['checks']))
    def test_manual_input_edit_detected(self):
        with tempfile.TemporaryDirectory() as td:
            s=self.setup_record(td); s['assumptions'].append('new assumption without revision')
            r=states.audit(s,DeliveryAuditChecks().requirements(),td)
            self.assertTrue(any(c['code']=='artifact.proposal.inputs' and c['status']=='unknown' for c in r['checks']))
    def test_record_failed_review_not_pass(self):
        with tempfile.TemporaryDirectory() as td:
            s=self.setup_record(td,'fail')
            self.assertEqual(states.audit(s,DeliveryAuditChecks().requirements(),td)['overall'],'fail')
    def test_verify_does_not_promote_status(self):
        with tempfile.TemporaryDirectory() as td:
            s=StateChecks().state(); s,_=states.update(s,'attendees',300,'fresh source')
            (Path(td)/'proposal.md').write_text('draft',encoding='utf8')
            out=states.record_verification(s,'proposal',td,DeliveryAuditChecks().requirements(),'reviewer','checked draft','pass')
            self.assertEqual(out['artifacts']['proposal']['status'],'stale')
    def test_multiple_records_do_not_stale_each_other(self):
        with tempfile.TemporaryDirectory() as td:
            s=StateChecks().state()
            for name in ('proposal','budget'): (Path(td)/(name+'.md')).write_text('300 attendees',encoding='utf8')
            req=DeliveryAuditChecks().requirements(); req['artifact_ids']=['proposal','budget']
            for name in req['artifact_ids']:
                s=states.record_verification(s,name,td,req,'reviewer','compare corresponding document','pass')
            self.assertEqual(s['input_revision'],1)
            self.assertEqual(s['revision'],3)
            self.assertEqual(states.audit(s,req,td)['overall'],'pass')
    def test_verification_requires_nonempty_file(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError): states.record_verification(StateChecks().state(),'proposal',td,DeliveryAuditChecks().requirements(),'reviewer','comparison','pass')
    def test_missing_coverage_review_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            s=self.setup_record(td); req=DeliveryAuditChecks().requirements(); req.pop('coverage_review')
            s=states.record_verification(s,'proposal',td,req,'reviewer','compare known inputs','pass')
            self.assertEqual(states.audit(s,req,td)['overall'],'unknown')
    def test_explicit_fact_review_result_propagates(self):
        with tempfile.TemporaryDirectory() as td:
            for result in ('fail','unknown'):
                s=self.setup_record(td); req=DeliveryAuditChecks().requirements(); req['facts']['attendees']['verification_result']=result
                s=states.record_verification(s,'proposal',td,req,'reviewer','compare evidence','pass')
                self.assertEqual(states.audit(s,req,td)['overall'],result)
    def test_invalid_fact_review_result_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            req=DeliveryAuditChecks().requirements(); req['facts']['attendees']['verification_result']='approved'
            with self.assertRaises(ValueError): states.audit(StateChecks().state(),req,td)
    def test_missing_verified_file_hash_not_pass(self):
        with tempfile.TemporaryDirectory() as td:
            s=self.setup_record(td); (Path(td)/'proposal.md').unlink()
            r=states.audit(s,DeliveryAuditChecks().requirements(),td)
            self.assertEqual(r['overall'],'fail')
            self.assertEqual(next(c['status'] for c in r['checks'] if c['code']=='artifact.proposal.hash'),'unknown')
    def test_upstream_file_edit_detected_in_complete_artifact_audit(self):
        with tempfile.TemporaryDirectory() as td:
            s=StateChecks().state(); req=DeliveryAuditChecks().requirements(); req['artifact_ids']=['proposal','budget']
            for aid in req['artifact_ids']:
                (Path(td)/(aid+'.md')).write_text('300 attendees',encoding='utf8')
                s=states.record_verification(s,aid,td,req,'reviewer','compare full dependency documents','pass')
            (Path(td)/'budget.md').write_text('500 attendees budget',encoding='utf8')
            r=states.audit(s,req,td)
            self.assertEqual(r['overall'],'fail')
            self.assertEqual(next(c['status'] for c in r['checks'] if c['code']=='artifact.budget.hash'),'fail')
            self.assertEqual(next(c['status'] for c in r['checks'] if c['code']=='artifact.proposal.hash'),'pass')
    def test_verify_cli_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'state.json'; states.save(p,StateChecks().state()); (Path(td)/'proposal.md').write_text('300 attendees',encoding='utf8')
            req=Path(td)/'requirements.json'; req.write_text(json.dumps(DeliveryAuditChecks().requirements()),encoding='utf8')
            r=subprocess.run([sys.executable,str(ROOT/'scripts/project_state.py'),'verify',str(p),'--artifact','proposal','--requirements',str(req),'--reviewer','test','--method','manual document comparison','--result','pass'],capture_output=True)
            self.assertEqual(r.returncode,0,r.stderr)
            saved=json.loads(p.read_text(encoding='utf8')); self.assertEqual(len(saved['artifacts']['proposal']['verification']['sha256']),64)
            self.assertEqual(states.audit(saved,DeliveryAuditChecks().requirements(),td)['overall'],'pass')

if __name__=='__main__': unittest.main(verbosity=2)
