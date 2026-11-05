"""Synthetic method-integration fixture. Not a language model or biological result."""
from copy import deepcopy
from pathlib import Path

from pertinfer_benchmark.freeze import export_public_workspace
from pertinfer_benchmark.io import file_hash, write_json


class ScriptedFixtureModel:
    """Deterministic test backend, restricted to the named synthetic task."""

    def next_action(self, request):
        if request["task"].get("qualification") != "synthetic" or request["task"].get("task_id") != "synthetic_chain":
            raise ValueError("scripted fixture supports only synthetic_chain, not biological tasks")
        graph = deepcopy(request["source_graph"])
        for node in graph["nodes"]:
            if node["taxon"]:
                node["taxon"] = 9606
        for relation in graph["relations"]:
            relation["support_class"] = "hypothesis"
            relation["evidence_ids"] = []
        step = request["step"]
        if step == 0:
            action = dict(type="tool", name="save_graph", arguments=dict(graph=graph, reason="Synthetic correspondence hypothesis"))
        elif step == 1:
            action = dict(type="tool", name="search_materials", arguments=dict(
                graph_path=request["latest_graph_path"], process={"relation_id": "e1", "kind": "activation"},
                keywords=["fixture:input", "fixture:mediator", "activation"]))
        elif step == 2:
            action = dict(type="tool", name="read_material", arguments={"material_id": "synthetic-source"})
        elif step == 3:
            action = dict(type="tool", name="add_evidence", arguments={"entry": dict(
                evidence_id="synthetic-evidence", claim="Explicit fictitious two-step relation for an integration test",
                support_class="direct_support", material_ids=["synthetic-source"], locator="synthetic text",
                context={"synthetic": True}, anchor_text="fixture:input activates fixture:mediator.")})
        elif step == 4:
            for relation in graph["relations"]:
                relation.update(evidence_ids=["synthetic-evidence"], support_class="direct_support")
            action = dict(type="tool", name="save_graph", arguments=dict(graph=graph, reason="Attach the synthetic source evidence", stage="revised"))
        else:
            action = dict(type="final", status="ok", graph_path=request["latest_graph_path"],
                          human_mapping=[{"basis": "Scripted synthetic fixture; not inferred biological correspondence"}],
                          answer="Synthetic integration check only. No model or biological inference performed.")
        return {"action": action, "model": "scripted-synthetic-fixture"}


def prepare_fixture(destination):
    # Curator fixture setup only. The live runner never imports dataset builders
    # or has access to this private reference-bearing release directory.
    from pertinfer_benchmark.demo import fixture_spec
    from pertinfer_benchmark.release import build_release
    from pertinfer_benchmark.materials import build
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False)
    build_release(fixture_spec(), root / "release")
    export_public_workspace(root / "release", root / "public", "masked")
    source = root / "synthetic-source.txt"
    source.write_text("This is invented text for a software test, not an experiment. "
                      "fixture:input activates fixture:mediator. "
                      "fixture:mediator increases fixture:output RNA expression.\n")
    build(dict(corpus_id="synthetic-agent-corpus-v1", task_id="synthetic_chain", sources=[dict(
        material_id="synthetic-source", title="Synthetic source", path=str(source.resolve()),
        format="text", mime_type="text/plain", source_url="https://example.invalid/synthetic-source",
        sha256=file_hash(source), source_kind="synthetic")]), root / "corpus")
    return root


def run_demo(destination):
    from .runner import run_agent
    root = prepare_fixture(destination)
    return run_agent(root / "public", "synthetic_chain", root / "corpus", root / "agent-run",
                     ScriptedFixtureModel(), method="pertinfer_synthetic_fixture", max_steps=6,
                     config={"backend": "scripted_fixture", "not_a_language_model": True})
