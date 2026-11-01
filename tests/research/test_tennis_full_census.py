import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
import sys
from uuid import UUID
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'/'research'))
import tennis_label_audit_resumable as census
from tennis_label_audit import (_usable_training_example,_example_rejection_reasons,
    _select_pre_result_snapshot,ODDS_FREE_V2_FEATURE_NAMES)


def test_numeric_or_temporal_failures_cannot_enter_usable_examples():
    row={'feature_availability':{'certified':True},
         'feature_values':{k:0.0 for k in ODDS_FREE_V2_FEATURE_NAMES},
         'snapshot_observed_at':'2026-10-20T12:00:00Z','label_available_at':None}
    assert _usable_training_example(row)  # evaluation candidate; not causal train
    assert not _usable_training_example({**row,'label_available_at':row['snapshot_observed_at']})
    assert 'snapshot_not_before_label' in _example_rejection_reasons(
        {**row,'label_available_at':'2026-10-20T11:00:00Z'})
    assert not _usable_training_example({**row,'feature_values':{**row['feature_values'],'games_a':float('nan')}})
    assert not _usable_training_example({**row,'feature_availability':{'certified':False}})


def test_resume_covers_entire_frozen_universe_and_matches_continuous_digest(tmp_path,monkeypatch):
    event_ids=[str(UUID(int=i)) for i in (1,2,3)]
    monkeypatch.setattr(census,'_freeze_event_ids',lambda *args:event_ids)
    def index(ids,path,**kwargs):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(''.join(json.dumps({'fonbet_event_id':str(i)})+'\n' for i in ids))
        return 0
    monkeypatch.setattr(census,'_write_snapshot_index',index)
    monkeypatch.setattr(census,'_load_full_snapshot_batch',lambda *args,**kwargs:[])
    class Query:
        def filter(self,*args): return self
        def with_entities(self,*args): return self
        def distinct(self): return self
        def count(self): return 0
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,*args): pass
        def query(self,*args): return Query()
    monkeypatch.setattr(census,'SessionLocal',Session)
    import tennis_corpus_counts
    monkeypatch.setattr(tennis_corpus_counts,'compute',lambda *args,**kwargs:{
        'documents_total':0,'availability_unknown_documents':0,'parsed_versions_total':0,
        'availability_unknown_versions':0,'source_matches_total':0})
    def batch_report(*,snapshot_index_path,training_examples_output_dir,**kwargs):
        assert kwargs['defer_admission_windows'] is True
        assert kwargs['filter_unusable_examples'] is True
        assert kwargs['preloaded_snapshot_rows']==[]
        event_id=json.loads(Path(snapshot_index_path).read_text())['fonbet_event_id']
        at=datetime(2026,10,20,tzinfo=timezone.utc)+timedelta(hours=UUID(event_id).int)
        rows=[{'target':target,'fonbet_event_id':event_id,'snapshot_id':'s-'+event_id,
               'label':UUID(event_id).int%2,'snapshot_observed_at':at.isoformat(),
               'label_available_at':(at+timedelta(minutes=30)).isoformat(),
               'feature_availability':{'certified':True},'feature_missing_fields':[]}
              for target in ('point','game','set','match')]
        diagnostics=[{'fonbet_event_id':event_id,'targets':{
            t:{'status':'feature_certified_candidate','additional_reasons':[]} for t in ('point','game','set','match')}}]
        targets={t:{'eligible_matches':1,'labelled_matches':1,'training_examples':1,
                    'class_a':int(rows[0]['label']==0),'class_b':int(rows[0]['label']==1)}
                 for t in ('point','game','set','match')}
        return {'audit_scope':{'indexed_snapshot_keys':0},'targets':targets,
            'feature_availability':{'checked_examples':4,'uncertified_examples':0,'feature_values_missing_examples':0},
            'target_diagnostics':{t:{'unique_matches_with_parsed_labels':1,
              'unique_matches_with_snapshot_aligned_labels':1,'unique_matches_with_feature_certified_labels':1,
              'stage_exclusions':{'feature_certified_candidate':1},'secondary_exclusions':{}} for t in targets},
            'training_examples_manifest':census._write_training_examples(rows,training_examples_output_dir),
            'match_exclusions_manifest':census._write_audit_jsonl(diagnostics,training_examples_output_dir,'diagnostics'),
            'rejected_examples_manifest':census._write_audit_jsonl(
                [{**rows[0],'rejection_reasons':['feature_value_missing:points_a']}] if UUID(event_id).int==1 else [],
                training_examples_output_dir,'rejected')}
    monkeypatch.setattr(census,'build_report',batch_report)
    cutoff=datetime(2026,10,28,tzinfo=timezone.utc)
    ticks=iter([0,0,2])
    monkeypatch.setattr(census,'time',SimpleNamespace(monotonic=lambda:next(ticks)))
    root=tmp_path/'resumed'
    partial=census.run_resumable(output_path=str(root/'report.json'),checkpoint_dir=str(root/'checkpoint'),
        cutoff=cutoff,event_batch_size=1,max_runtime_seconds=1)
    assert partial['audit_scope']['completed_events']==1
    assert partial['audit_scope']['total_events']==3
    assert partial['training_gate']['status']=='blocked'
    assert partial['training_gate']['windows_evaluated'] is False
    monkeypatch.setattr(census.time,'monotonic',lambda:0)
    resumed=census.run_resumable(output_path=str(root/'report.json'),checkpoint_dir=str(root/'checkpoint'),
        cutoff=cutoff,event_batch_size=1,max_runtime_seconds=1,resume=True)
    other=tmp_path/'continuous'
    continuous=census.run_resumable(output_path=str(other/'report.json'),checkpoint_dir=str(other/'checkpoint'),
        cutoff=cutoff,event_batch_size=1,max_runtime_seconds=1)
    assert resumed['rejected_example_reasons_by_target']['point']['feature_value_missing:points_a']==1
    assert resumed['target_diagnostics']['point']['secondary_exclusions']['incomplete_state']==1
    assert resumed['audit_scope']['scanned_event_rows']==3
    assert resumed['audit_scope']['full_database_census'] is True
    assert resumed['training_examples_manifest']['logical_digest']==continuous['training_examples_manifest']['logical_digest']
    assert resumed['training_gate']['manifest']['dataset_digest']==continuous['training_gate']['manifest']['dataset_digest']
    assert resumed['training_gate']['manifest']['holdout_quality_metrics_opened'] is False
