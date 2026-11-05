import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pertinfer_agent.demo import ScriptedFixtureModel, prepare_fixture, run_demo
from pertinfer_agent.runner import corpus_binding, run_agent
from pertinfer_agent.transport import CommandModel
from pertinfer_benchmark.freeze import verify_predictions
from pertinfer_benchmark.io import content_hash, file_hash, read_json, write_json


@pytest.fixture
def fixture(tmp_path):
    return prepare_fixture(tmp_path / "fixture")


def execute(fixture, model, **kwargs):
    return run_agent(fixture / "public", "synthetic_chain", fixture / "corpus", fixture / "agent",
                     model, **kwargs)


def test_demo_revises_graph_reads_evidence_and_exports_prediction(tmp_path):
    root = tmp_path / "demo"
    report = run_demo(root)
    assert report["status"] == "ok" and report["model_calls"] == 6
    assert report["cost"] is None and all(r["provider_usage"] is None for r in report["usage"])
    history = [json.loads(x) for x in (root / "agent-run/graphs/history.jsonl").read_text().splitlines()]
    assert len(history) == 2 and history[1]["parent_sha256"] == history[0]["sha256"]
    events = [json.loads(x) for x in (root / "agent-run/tool_audit/events.jsonl").read_text().splitlines()]
    search = next(x for x in events if x["tool"] == "search_materials")
    assert search["result"]["results"]
    verify_predictions(root / "agent-run/predictions")
    seal = read_json(root / "agent-run/run_manifest.json")
    assert content_hash(seal["files"]) == seal["manifest_hash"]
    for path, digest in seal["files"].items():
        assert file_hash(root / "agent-run" / path) == digest
    from pertinfer_benchmark.runner import run
    scores = run(root / "release", root / "agent-run/predictions", root / "scores.json")
    assert scores["rows"][0]["lower"] == 1


def test_budget_exhaustion_is_sealed_failure(fixture):
    result = execute(fixture, ScriptedFixtureModel(), max_steps=1)
    assert result["status"] == "resource_limit"
    p = read_json(fixture / "agent/predictions/prediction.json")
    assert p["status"] == "resource_limit" and p["graph"]["relations"] == []
    verify_predictions(fixture / "agent/predictions")


def test_model_crash_is_preserved_and_counted(fixture):
    class Broken:
        def next_action(self, request):
            raise RuntimeError("synthetic provider failure")
    result = execute(fixture, Broken())
    assert result["status"] == "crash" and result["model_calls"] == 1
    assert result["usage"][0]["provider_usage"] is None
    assert result["failure"]["type"] == "RuntimeError"
    verify_predictions(fixture / "agent/predictions")


def test_timeout_is_resource_limit(fixture):
    class Timeout:
        def next_action(self, request):
            raise TimeoutError("test timeout")
    assert execute(fixture, Timeout())["status"] == "resource_limit"


def test_explicit_abstention_requires_no_fabricated_graph(fixture):
    class Abstain:
        def next_action(self, request):
            return {"action": {"type": "final", "status": "no_path", "answer": "Insufficient evidence"}, "usage": {"input_tokens": 5}}
    result = execute(fixture, Abstain())
    assert result["status"] == "no_path" and result["usage"][0]["provider_usage"] == {"input_tokens": 5}


def test_final_graph_cannot_escape_workspace(fixture):
    class Escape:
        def next_action(self, request):
            return {"action": {"type": "final", "status": "ok", "graph_path": str(fixture / "public/public/synthetic_chain/source_graph.json")}}
    result = execute(fixture, Escape())
    assert result["status"] == "invalid"
    assert result["failure"]["type"] == "PermissionError"


def test_reader_errors_can_be_repaired_and_online_calls_refused(fixture, monkeypatch):
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("offline evidence opened a socket"))
    class Repair:
        def next_action(self, request):
            if request["step"] == 0:
                return {"action": {"type": "tool", "name": "archive_url", "arguments": {"url": "https://example.org"}}}
            assert request["history"][-1]["observation"]["status"] == "error"
            return {"action": {"type": "final", "status": "no_path", "answer": "Offline source unavailable"}}
    assert execute(fixture, Repair())["status"] == "no_path"


def test_corpus_identity_and_asset_integrity_checked_before_model(fixture):
    corpus = read_json(fixture / "corpus/corpus.json")
    corpus["task_id"] = "wrong-task"
    write_json(fixture / "corpus/corpus.json", corpus)
    with pytest.raises(ValueError, match="task identity"):
        execute(fixture, ScriptedFixtureModel())
    assert not (fixture / "agent").exists()
    corpus["task_id"] = "synthetic_chain"
    write_json(fixture / "corpus/corpus.json", corpus)
    asset = fixture / "corpus" / corpus["materials"][0]["artifacts"][0]["path"]
    asset.write_text("changed")
    with pytest.raises(ValueError, match="integrity"):
        corpus_binding(fixture / "corpus", "synthetic_chain")


def test_corpus_mutation_during_inference_invalidates_output(fixture):
    class Change:
        def next_action(self, request):
            p = fixture / "corpus/corpus.json"
            obj = read_json(p)
            obj["corpus_id"] = "changed"
            write_json(p, obj)
            return {"action": {"type": "final", "status": "no_path", "answer": "fixture"}}
    assert execute(fixture, Change())["status"] == "invalid"


def test_output_does_not_overwrite_inputs_or_previous_runs(fixture):
    with pytest.raises(ValueError, match="disjoint"):
        run_agent(fixture / "public", "synthetic_chain", fixture / "corpus", fixture / "public/run", ScriptedFixtureModel())
    execute(fixture, ScriptedFixtureModel(), max_steps=1)
    with pytest.raises(FileExistsError):
        execute(fixture, ScriptedFixtureModel())


def test_resealed_public_input_change_during_inference_is_rejected(fixture):
    class Change:
        def next_action(self, request):
            root = fixture / "public"
            manifest = read_json(root / "public_run_manifest.json")
            path = "public/synthetic_chain/source_graph.json"
            graph = read_json(root / path)
            graph["notes"] = "Changed after registration"
            write_json(root / path, graph)
            manifest["files"][path] = file_hash(root / path)
            write_json(root / "public_run_manifest.json", manifest)
            return {"action": {"type": "final", "status": "no_path"}}
    result = execute(fixture, Change())
    assert result["status"] == "invalid"
    assert result["failure"]["message"] == "public inputs changed during inference"


def test_command_model_protocol_and_error_redaction():
    model = CommandModel([sys.executable, "-c", 'import json,sys; x=json.load(sys.stdin); print(json.dumps({"action":{"type":"final","status":"no_path"},"usage":{"requests":1}}))'])
    assert model.next_action({"task": {}})["usage"] == {"requests": 1}
    model = CommandModel([sys.executable, "-c", 'import sys; print("SECRET",file=sys.stderr); sys.exit(2)'])
    with pytest.raises(RuntimeError, match="code 2") as error:
        model.next_action({})
    assert "SECRET" not in str(error.value)
    model = CommandModel([sys.executable, "-c", 'print("not-json")'])
    with pytest.raises(ValueError, match="JSON object"):
        model.next_action({})


def test_command_timeout():
    model = CommandModel([sys.executable, "-c", 'import time; time.sleep(10)'], timeout=0.1)
    with pytest.raises(TimeoutError):
        model.next_action({})


def test_runtime_imports_no_evaluator():
    env = dict(os.environ)
    subprocess.run([sys.executable, "-c", 'import sys; import pertinfer_agent.runner; assert not any(n in sys.modules for n in ["pertinfer_benchmark.evaluate","pertinfer_benchmark.runner","pertinfer_benchmark.scoring","pertinfer_benchmark.release"])'], check=True, env=env)


def test_model_request_contains_only_public_inputs(fixture):
    class Inspect:
        def next_action(self, request):
            text = json.dumps({k: v for k, v in request.items() if k != "instructions"})
            assert "reference_chains" not in text and "private/" not in text
            assert "archive_url" not in request["tools"]
            assert "allowed_context" not in request
            return {"action": {"type": "final", "status": "no_path"}}
    assert execute(fixture, Inspect())["status"] == "no_path"
