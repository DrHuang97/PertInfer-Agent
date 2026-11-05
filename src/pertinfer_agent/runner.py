"""Portable model/tool loop using public benchmark interfaces only.

This integration wrapper is new. Historical dynamic experiment scheduling and
reference scoring are deliberately not imported or reproduced here.
"""
from importlib.resources import files
from pathlib import Path
import time

from pertinfer_benchmark import export_prediction, load_public_task, validate_graph
from pertinfer_benchmark.freeze import freeze_predictions, verify_public_workspace
from pertinfer_benchmark.io import content_hash, file_hash, read_json, write_json
from pertinfer_benchmark.tasks import validate_fixed_boundaries

from .e2e_tools import CATALOG, E2ETools
from .material_search import MaterialSearch

EMPTY_GRAPH = {"nodes": [], "reactions": [], "relations": []}
OFFLINE_TOOLS = {k: v for k, v in CATALOG.items() if k not in ("archive_url", "online_research")}


def corpus_binding(root, task_id):
    """Verify the declared corpus and its original assets before a model runs."""
    root = Path(root).resolve()
    index = read_json(root / "corpus.json")
    if index.get("task_id") != task_id:
        raise ValueError("corpus task identity does not match requested task")
    search = MaterialSearch(root)
    assets = {}
    for material in search.materials.values():
        for asset in material.get("artifacts", []):
            path = (root / asset["path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("corpus asset outside declared root or missing")
            digest = file_hash(path)
            if digest != asset["sha256"] or path.stat().st_size != asset["bytes"]:
                raise ValueError("corpus asset integrity mismatch")
            assets[path.relative_to(root).as_posix()] = digest
    return {"corpus_id": index["corpus_id"], "corpus_sha256": search.corpus_sha256, "assets": assets}


def run_agent(public, task_id, corpus, workspace, model, *, method="pertinfer", seed=0,
              max_steps=40, config=None):
    public, corpus, workspace = (Path(p).resolve() for p in (public, corpus, workspace))
    if any(workspace.is_relative_to(p) or p.is_relative_to(workspace) for p in (public, corpus)):
        raise ValueError("workspace must be disjoint from public inputs and corpus")
    if type(max_steps) is not int or max_steps < 1 or type(seed) is not int or not isinstance(method, str) or not method:
        raise ValueError("invalid method, seed or step budget")
    manifest = verify_public_workspace(public)
    bundle = load_public_task(public, task_id, manifest["view"])
    cohort = read_json(public / "cohorts.json")["tasks"][task_id]
    corpus_start = corpus_binding(corpus, task_id)
    workspace.mkdir(parents=True, exist_ok=False)
    for key in ("task", "source_graph", "source_provenance"):
        write_json(workspace / "input" / f"{key}.json", bundle[key])
    instructions = files("pertinfer_agent").joinpath("instructions.md").read_text(encoding="utf-8")
    configuration = {"max_steps": max_steps, "method": method, "seed": seed, "model": config or {}}
    registration = dict(task_id=task_id, configuration=configuration,
                        release_hash=manifest["release_hash"], input_hash=bundle["input_hash"],
                        cohort_hash=content_hash(cohort), corpus=corpus_start,
                        instructions_hash=content_hash(instructions), evidence_mode="offline")
    write_json(workspace / "registration.json", registration)
    prediction_root = workspace / "predictions"
    write_json(prediction_root / "run_plan.json", dict(release_hash=manifest["release_hash"], expected_runs=[
        dict(task_id=task_id, method=method, view=manifest["view"], seed=seed)]))
    tools = E2ETools(corpus, workspace)
    history, usage, latest, graph, final = [], [], None, dict(EMPTY_GRAPH), {}
    status, failure = "resource_limit", None
    started = time.monotonic()
    for step in range(max_steps):
        request = dict(protocol="pertinfer_agent.action_v1", instructions=instructions,
                       task=bundle["task"], source_graph=bundle["source_graph"],
                       source_provenance=bundle["source_provenance"], tools=OFFLINE_TOOLS,
                       history=history, latest_graph_path=latest, step=step, steps_remaining=max_steps-step)
        write_json(workspace / "model" / f"request_{step:04d}.json", request)
        usage.append({"step": step, "provider_usage": None, "reported_model": None})
        try:
            response = model.next_action(request)
            if not isinstance(response, dict) or not isinstance(response.get("action"), dict):
                raise ValueError("model response requires an action object")
            write_json(workspace / "model" / f"response_{step:04d}.json", response)
            usage[-1].update(provider_usage=response.get("usage"), reported_model=response.get("model"))
            action = response["action"]
            if action.get("type") == "tool":
                # Unknown or online actions return a recorded refusal; the model can repair.
                name = action.get("name")
                if name not in OFFLINE_TOOLS:
                    result = tools.call("__disallowed_action__", {"requested_tool": name})
                else:
                    result = tools.call(name, action.get("arguments", {}))
                if name == "save_graph" and result.get("status") == "ok":
                    latest = result["path"]
                history.append({"action": action, "observation": result})
                continue
            if action.get("type") != "final" or action.get("status") not in ("ok", "no_path"):
                raise ValueError("expected tool or final action with ok/no_path status")
            final = action
            status = action["status"]
            if status == "ok":
                candidate_path = tools._workspace_path(action["graph_path"])
                revisions = tools.call("graph_history", {})
                if not any(Path(r["path"]).resolve() == candidate_path and r["sha256"] == file_hash(candidate_path)
                           for r in revisions.get("versions", [])):
                    raise ValueError("final graph must be an unmodified saved graph version")
                check = tools.call("validate_graph", {"graph_path": str(candidate_path), "require_evidence": True})
                if check.get("status") != "ok":
                    raise ValueError("final graph failed evidence-reference validation")
                graph = read_json(candidate_path)
                validate_graph(graph)
                validate_fixed_boundaries(bundle["task"], graph)
            break
        except TimeoutError as exc:
            status, failure = "resource_limit", {"type": type(exc).__name__, "message": str(exc)}
            break
        except Exception as exc:
            status = "invalid" if final else "crash"
            failure = {"type": type(exc).__name__, "message": str(exc)}
            break
    # Check immutable input bindings before sealing. A failure is an output status,
    # never a successful prediction with altered evidence or public inputs.
    try:
        if verify_public_workspace(public) != manifest:
            raise ValueError("public inputs changed during inference")
        if corpus_binding(corpus, task_id) != corpus_start:
            raise ValueError("corpus changed during inference")
    except Exception as exc:
        status, failure = "invalid", {"type": type(exc).__name__, "message": str(exc)}
    if status != "ok":
        graph = dict(EMPTY_GRAPH)
    write_json(workspace / "output/final_human_graph.json", graph)
    write_json(workspace / "output/human_mapping.json", final.get("human_mapping", []))
    write_json(workspace / "output/answer.json", {"status": status, "answer": final.get("answer"), "failure": failure})
    ledger = tools.call("write_ledger", {})
    if ledger.get("status") != "ok":
        raise RuntimeError("evidence ledger export failed")
    export_prediction(prediction_root / "prediction.json", task_id=task_id, method=method,
                      view=manifest["view"], seed=seed, graph=graph, status=status,
                      release_hash=manifest["release_hash"], knowledge_hash=bundle["view_manifest"]["knowledge_hash"],
                      input_hash=bundle["input_hash"], cohort_hash=content_hash(cohort),
                      draft_hash=content_hash({}), config_hash=content_hash(configuration),
                      corpus_hash=content_hash(corpus_start), agent_registration_hash=content_hash(registration))
    freeze_predictions(prediction_root, [task_id], prediction_root / "run_plan.json")
    report = dict(status=status, task_id=task_id, failure=failure, model_calls=len(usage),
                  elapsed_seconds=time.monotonic()-started, usage=usage,
                  usage_note="Provider metadata when supplied; absent measurements remain null", cost=None,
                  corpus_hash=content_hash(corpus_start), prediction_directory=str(prediction_root))
    write_json(workspace / "run.json", report)
    if tools.call("manifest", {}).get("status") != "ok":
        raise RuntimeError("tool manifest export failed")
    # Include the last tool receipt, unlike the tool's pre-receipt manifest.
    outer = {p.relative_to(workspace).as_posix(): file_hash(p) for p in sorted(workspace.rglob("*"))
             if p.is_file() and not p.is_symlink() and p.name != "run_manifest.json"}
    write_json(workspace / "run_manifest.json", {"files": outer, "manifest_hash": content_hash(outer)})
    return report
