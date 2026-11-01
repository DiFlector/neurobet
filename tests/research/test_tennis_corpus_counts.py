from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/research'))
import tennis_corpus_counts as counts


def test_counts_resume_and_keep_versions_and_distinct_sources(tmp_path,monkeypatch):
    cutoff=datetime(2026,10,28,tzinfo=timezone.utc)
    def row(n,available,source=None):
        return SimpleNamespace(id=UUID(int=n),retrieved_at=cutoff-timedelta(days=1),
                               available_at=available,source_match_id=source)
    data={'documents':[row(1,None),row(2,cutoff)],
          'versions':[row(1,None,'a'),row(2,cutoff,'a'),row(3,cutoff+timedelta(days=1),'b')]}
    state={'calls':0,'interrupt':True}
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,query,params=None):
            if params is None: return None
            state['calls']+=1
            if state['interrupt'] and state['calls']==3: raise TimeoutError('interrupted')
            kind='documents' if 'tennis_point_documents' in str(query) else 'versions'
            rows=[r for r in data[kind] if not params['cursor'] or r.id>params['cursor']]
            return SimpleNamespace(all=lambda:rows[:params['page_size']])
    monkeypatch.setattr(counts,'SessionLocal',Session)
    path=tmp_path/'counts.sqlite'
    with pytest.raises(TimeoutError): counts.compute(path,cutoff=cutoff,page_size=2)
    assert counts.read_state(path)['documents_total']==2
    state['interrupt']=False
    result=counts.compute(path,cutoff=cutoff,page_size=2)
    assert result['complete'] and result['documents_total']==2
    assert result['availability_unknown_documents']==1
    assert result['scanned_versions']==3 and result['parsed_versions_total']==1
    assert result['availability_unknown_versions']==1 and result['source_matches_total']==2
    assert counts.compute(path,cutoff=cutoff,page_size=2)==result
    with pytest.raises(ValueError,match='cutoff_mismatch'):
        counts.compute(path,cutoff=cutoff+timedelta(days=1),page_size=2)
