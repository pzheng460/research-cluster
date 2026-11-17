from datetime import datetime, timezone, timedelta
import json

import pytest

from research_cluster import literature
from research_cluster.models import Study
from research_cluster.research import research_step


def papers():
    return [{"id": identity, "version": identity+'v1', "title": title, "source": "arxiv", "verified": True,
        "published": "2026-10-20T00:00:00Z", "updated": "2026-10-21T00:00:00Z", "authors": ["Test Author"],
        "url": f"https://arxiv.org/abs/{identity}", "abstract": "A testable mechanism for speculative drafting.",
        "read_depth": "abstract", "foundational": False} for identity,title in
        [("2610.11111", "Parallel Drafting Research"), ("2610.22222", "Acceptance-Aware Training")]]


def fake_collection(study):
    return {"papers": papers(), "query": 'all:"DSpark"', "query_terms": ["DSpark"], "searched_at": 100,
            "coverage": "Test primary-source metadata"}


@pytest.fixture
def offline_literature(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(literature, "collect", fake_collection)
    def no_html(_):
        raise ValueError("HTML unavailable in this fixture")
    monkeypatch.setattr(literature, "read_excerpt", no_html)


def create_and_plan(client, app):
    response = client.post('/api/studies', json={"name": "DSpark research", "objective": "Explore architecture under a fixed inference budget"})
    assert response.status_code == 201, response.text
    identity = response.json()["id"]
    research_step(app.state.db, app.state.settings, identity)
    return client.get(f'/api/studies/{identity}').json()


def test_new_research_defaults_to_literature_not_benchmark(client):
    response = client.post('/api/studies', json={"name": "new research", "objective": "Investigate DSpark"})
    assert response.status_code == 201
    study = response.json()
    assert study['mode'] == 'literature'
    assert study['config']['literature']['phase'] == 'queued'
    assert client.get('/api/state').json()['jobs'] == []
    assert client.get('/api/health').json()['research_default'] == 'literature_first'


def test_planning_creates_evidence_tasks_without_compute(client, app, offline_literature, monkeypatch):
    calls = []
    monkeypatch.setattr(literature, 'collect', lambda study: (calls.append(study.id) or fake_collection(study)))
    study = create_and_plan(client, app)
    evidence = study['config']['literature']
    assert evidence['phase'] == 'awaiting_review' and evidence['method'] == 'evidence_checklist'
    assert len(evidence['papers']) == 2 and len(evidence['tasks']) >= 2
    assert all(set(task['evidence_ids']) <= {'2610.11111','2610.22222'} for task in evidence['tasks'])
    research_step(app.state.db, app.state.settings, study['id'])
    assert calls == [study['id']]
    assert client.get('/api/state').json()['jobs'] == []


def test_accepting_a_proposal_does_not_launch_an_experiment(client, app, offline_literature):
    study = create_and_plan(client, app)
    task = study['config']['literature']['tasks'][0]
    result = client.post(f"/api/studies/{study['id']}/proposals/{task['id']}/accept")
    assert result.status_code == 200
    assert result.json()['config']['literature']['tasks'][0]['status'] == 'accepted'
    assert client.get('/api/state').json()['jobs'] == []
    denied = client.put(f"/api/studies/{study['id']}/proposals/{task['id']}/execution", json={'name':'execution','objective':'Verify selected hypothesis'})
    assert denied.status_code == 422


def test_synthesis_cannot_cite_unretrieved_papers(client, app, offline_literature):
    study = create_and_plan(client, app)
    task = study['config']['literature']['tasks'][0]
    task['evidence_ids'] = ['2610.99999']
    response = client.put(f"/api/studies/{study['id']}/synthesis", json={'summary':'A reviewed synthesis with an invalid evidence citation.','tasks':[task]})
    assert response.status_code == 422


def test_reviewed_synthesis_retains_accepted_tasks_on_refresh(client, app, offline_literature):
    study = create_and_plan(client, app)
    tasks = study['config']['literature']['tasks']
    client.post(f"/api/studies/{study['id']}/proposals/{tasks[0]['id']}/accept")
    response = client.put(f"/api/studies/{study['id']}/synthesis", json={'summary':'A revised cross-paper research synthesis.','tasks':tasks[1:]})
    assert response.status_code == 200
    updated = response.json()['config']['literature']
    assert updated['method'] == 'assistant_reviewed'
    assert any(task['id'] == tasks[0]['id'] and task['status'] == 'accepted' for task in updated['tasks'])


def test_cancellation_fences_a_pending_literature_result(client, app, offline_literature, monkeypatch):
    def canceled_collection(study):
        client.post(f'/api/studies/{study.id}/cancel')
        return fake_collection(study)
    monkeypatch.setattr(literature, 'collect', canceled_collection)
    study = create_and_plan(client, app)
    assert study['status'] == 'canceled'
    assert not study['config']['literature'].get('tasks')


def feed(identity='2610.12345v2', published='2026-10-10T00:00:00Z', updated='2026-10-20T00:00:00Z'):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/{identity}</id><title>Test Paper</title><summary>Test evidence.</summary><published>{published}</published><updated>{updated}</updated><author><name>A</name></author></entry></feed>'''


def test_metadata_keeps_submission_and_revision_dates_and_excludes_future():
    current = datetime(2026,11,3,tzinfo=timezone.utc)
    result = literature.parse_feed(feed(), cutoff=current-timedelta(days=90), current=current)
    assert result[0]['id'] == '2610.12345' and result[0]['version'] == '2610.12345v2'
    assert result[0]['published'] != result[0]['updated']
    assert literature.parse_feed(feed(updated='2026-12-01T00:00:00Z'), current=current) == []


def test_old_papers_require_explicit_foundational_selection():
    current=datetime(2026,11,3,tzinfo=timezone.utc)
    old=feed(published='2026-01-01T00:00:00Z',updated='2026-01-01T00:00:00Z')
    cutoff=current-timedelta(days=90)
    assert literature.parse_feed(old,cutoff=cutoff,current=current) == []
    assert literature.parse_feed(old,cutoff=cutoff,current=current,foundational=True)[0]['foundational']


def test_local_evidence_is_scoped_and_hashed(client, app, tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'findings.json').write_text(json.dumps({'status':'No throughput gain','winner':{'scope':'pilot only'}}))
    response=client.post('/api/studies',json={'name':'local','objective':'Read prior negative findings','repo_path':str(repo),'context_paths':['findings.json']})
    with app.state.db.transaction(write=False) as session:
        study=session.get(Study,response.json()['id'])
    evidence=literature.local_context(study,app.state.settings)
    assert len(evidence[0]['sha256']) == 64 and 'No throughput gain' in evidence[0]['excerpt']
    (repo/'escape.txt').symlink_to(tmp_path/'outside.txt')
    with app.state.db.transaction() as session:
        study=session.get(Study,study.id)
        study.config={**study.config,'context_paths':['escape.txt']}
    with pytest.raises(ValueError,match='outside'):
        literature.local_context(study,app.state.settings)


def test_non_benchmark_openhands_requests_also_start_with_literature(client, offline_literature):
    response=client.post('/api/studies',json={'name':'agent research','objective':'Review papers before coding','mode':'openhands'})
    assert response.status_code == 201
    assert response.json()['config']['literature']['phase'] == 'queued'


def test_accepted_proposal_launch_inherits_evidence_and_is_idempotent(client, app, offline_literature, monkeypatch, tmp_path):
    from research_cluster import api, research
    study=create_and_plan(client,app)
    task=study['config']['literature']['tasks'][0]
    client.post(f"/api/studies/{study['id']}/proposals/{task['id']}/accept")
    monkeypatch.setenv('LLM_MODEL','test/model')
    monkeypatch.setenv('LLM_API_KEY','unused-test-key')
    original=api.importlib.util.find_spec
    monkeypatch.setattr(api.importlib.util,'find_spec',lambda name,package=None: True if name=='openhands' else original(name,package))
    monkeypatch.setattr(research,'validate_repository',lambda *args: {'repo_path':str(tmp_path),'base_commit':'test-commit','protected_files':['evaluate.py']})
    body={'name':'approved experiment','objective':task['hypothesis'],'repo_path':str(tmp_path),'mode':'openhands'}
    path=f"/api/studies/{study['id']}/proposals/{task['id']}/execution"
    first=client.put(path,json=body)
    assert first.status_code == 201, first.text
    child=first.json()
    assert client.put(path,json=body).json()['id'] == child['id']
    assert child['mode']=='openhands' and child['config']['literature']['phase']=='execution_ready'
    assert child['config']['literature']['selected_task']['evidence_ids'] == task['evidence_ids']
    assert client.get('/api/state').json()['jobs'] == []
