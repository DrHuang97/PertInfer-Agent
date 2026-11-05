import hashlib
import json
import socket

import pytest

from pertinfer_agent.material_search import MaterialSearch


@pytest.fixture
def corpus(tmp_path):
    raw = b"Original source bytes"
    (tmp_path / "source.txt").write_bytes(raw)
    materials = [
        {"material_id": "positive", "title": "Experiment A", "text": "Alpha binds Beta in cells.", "source_url": "https://example.org/a"},
        {"material_id": "negative", "title": "Experiment B", "text": "Alpha binding to Beta was not detected in the alternative context.", "source_url": "https://example.org/b"},
        {"material_id": "other", "title": "Experiment C", "text": "Gamma phosphorylates Delta.", "source_url": "https://example.org/c"},
    ]
    for m in materials:
        m["artifacts"] = [{"path": "source.txt", "mime_type": "text/plain", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}]
    (tmp_path / "corpus.json").write_text(json.dumps({"corpus_id": "fixture", "materials": materials,
        "entity_aliases": [["Alpha", "A1", "UniProt:P00001"], ["Beta", "B1"]]}))
    return MaterialSearch(tmp_path)


def graph():
    return {"nodes": [{"id": "a", "entity": "symbol:A1"}, {"id": "b", "gene": "B1"}],
            "relations": [{"id": "proposed", "source": "a", "target": "b", "kind": "binding"}]}


def test_graph_process_search_returns_support_and_conflict_without_network(corpus, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("offline tool opened a socket"))
    out = corpus.search(graph(), {"relation_id": "proposed", "kind": "binding"}, ["Alpha", "Beta", "binding"])
    assert out["status"] == "ok"
    assert {r["material_id"] for r in out["results"]} == {"positive", "negative"}
    assert out["query_validation"]["biological_truth_checked"] is False
    assert out["network_used"] is False
    assert corpus.read_material("negative")["text"].startswith("Alpha binding")


@pytest.mark.parametrize("process,keywords", [
    ({"node_ids": ["absent"], "kind": "binding"}, "Alpha Beta binding"),
    ({"relation_id": "proposed", "kind": "binding"}, "Gamma Delta binding"),
    ({"relation_id": "proposed", "kind": "binding"}, "Alpha Beta weather"),
    ({"node_ids": ["a"], "kind": ""}, "Alpha binding"),
])
def test_unrelated_or_ungrounded_queries_return_repairable_error(corpus, process, keywords):
    out = corpus.search(graph(), process, keywords)
    assert out["status"] == "invalid_query" and not out["results"]
    assert not out["query_validation"]["biological_truth_checked"]


def test_new_hypothesis_does_not_need_reference_membership(corpus):
    g = graph(); g["nodes"].append({"id": "new", "gene": "Novel"})
    out = corpus.search(g, {"node_ids": ["new"], "kind": "phosphorylation"}, "Novel phosphorylation")
    assert out["status"] == "no_match"
    assert "unknown" in out["empty_means"]


def test_custom_biochemical_process_can_be_declared(corpus):
    out = corpus.search(graph(), {"node_ids": ["a"], "kind": "new_process", "process_terms": ["dimerization"]}, "Alpha dimerization")
    assert out["status"] == "no_match"


def test_material_bytes_are_checked_and_cannot_escape_corpus(corpus, tmp_path):
    (tmp_path / "source.txt").write_bytes(b"modified")
    with pytest.raises(ValueError, match="integrity"):
        corpus.read_material("positive")
    corpus.materials["positive"]["artifacts"][0]["path"] = "../outside.txt"
    with pytest.raises(ValueError, match="inside corpus"):
        corpus.read_material("positive")


def test_original_section_text_and_locator_preserved(corpus):
    corpus.materials["positive"]["sections"] = [{"id": "Fig1", "title": "Figure 1", "text": "Alpha binds Beta; only condition A."}]
    out = corpus.search(graph(), {"node_ids": ["a", "b"], "kind": "binding"}, "Alpha Beta binding")
    hit = next(x for x in out["results"] if x["material_id"] == "positive")
    assert hit["section"] == "Fig1"
    assert corpus.read_material("positive", "Fig1")["text"] == hit["excerpt"]
    with pytest.raises(ValueError, match="Unknown section"):
        corpus.read_material("positive", "not-a-section")


def test_search_pagination_and_source_filters_preserve_query_grounding(corpus):
    process = {"relation_id": "proposed", "kind": "binding"}
    first = corpus.search(graph(), process, "Alpha Beta binding", limit=1)
    assert first["has_more"] and first["next_offset"] == 1
    second = corpus.search(graph(), process, "Alpha Beta binding", limit=1, offset=first["next_offset"])
    assert not second["has_more"]
    assert first["results"][0]["material_id"] != second["results"][0]["material_id"]
    filtered = corpus.search(graph(), process, "Alpha Beta binding", filters={"source": "Experiment B"})
    assert [x["material_id"] for x in filtered["results"]] == ["negative"]
    rejected = corpus.search(graph(), process, "unrelated words", filters={"material_id": "positive"})
    assert rejected["status"] == "invalid_query"


def test_chemical_identity_and_identifier_only_homology_are_reachable(corpus):
    corpus.aliases.extend([{"Drug", "CHEMBL1"}, {"A1", "ENSG00001"}])
    corpus.materials["chemical"] = {"material_id": "chemical", "title": "ChEMBL original", "text": '{"molecules":[{"pref_name":"Drug","molecule_chembl_id":"CHEMBL1"}]}'}
    corpus.materials["homology"] = {"material_id": "homology", "title": "Ensembl original", "text": '{"source":{"id":"ENSG00001"},"type":"ortholog_one2one"}'}
    drug_graph = {"nodes": [{"id": "drug", "label": "Drug"}]}
    chem = corpus.search(drug_graph, {"node_ids": ["drug"], "kind": "identity"}, "Drug molecule", filters={"source": "ChEMBL"})
    assert [x["material_id"] for x in chem["results"]] == ["chemical"]
    homology = corpus.search(graph(), {"node_ids": ["a"], "kind": "orthology"}, "A1 orthology", filters={"source": "Ensembl"})
    assert [x["material_id"] for x in homology["results"]] == ["homology"]
