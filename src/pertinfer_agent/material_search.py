"""Offline, graph-grounded retrieval of source materials, independent of gold graphs.

Query validation establishes relevance to an agent-proposed process, never whether
that process is biologically true. No model, network, private reference or answer
key is used. A corpus is an explicit, immutable snapshot of original sources.
"""
from __future__ import annotations

import hashlib
import itertools
import unicodedata
from functools import lru_cache
import json
import re
from pathlib import Path

VERSION = "1.14.1"
PROCESS_TERMS = {
    "identity": ["identity", "rna", "mrna", "transcript", "transcripts", "identifier", "gene", "protein", "symbol", "uniprot", "molecule", "molecules", "molecular", "chemical", "compound", "身份", "标识", "基因", "蛋白", "分子", "化学", "化合物"],
    "orthology": ["orthology", "ortholog", "orthologs", "orthologous", "orthologue", "orthologues", "homology", "homolog", "homologs", "homologous", "同源", "直系同源"],
    "binding": ["binding", "bind", "binds", "bound", "affinity", "ligand", "interaction", "interactions", "interacts", "association", "complex", "结合", "亲和", "相互作用"],
    "activation": ["activation", "activate", "activates", "activated", "agonist", "agonism", "receptor", "激活", "激动剂", "受体"],
    "inhibition": ["inhibition", "inhibitor", "inhibitors", "silencing", "silenced", "inhibit", "inhibits", "inhibited", "antagonist", "antagonism", "抑制", "拮抗"],
    "transcription": ["transcription", "transcriptional", "expression", "rna", "mrna", "induction", "coactivator", "coactivation", "转录", "表达", "共激活"],
    "phosphorylation": ["phosphorylation", "phosphorylate", "phosphorylates", "phosphorylated", "phospho", "kinase", "磷酸化", "激酶"],
    "occupancy": ["occupancy", "chip", "chromatin", "recruitment", "recruit", "promoter", "占位", "染色质", "招募", "启动子"],
    "catalysis": ["catalysis", "metabolism", "metabolic", "formation", "ligation", "ligate", "ligated", "ligase", "hydroxylation", "hydroxylate", "hydroxylated", "羟化", "biosynthesis", "synthesis", "synthesize", "synthesizes", "production", "生物合成", "合成", "catalytic", "enzyme", "substrate", "product", "reaction", "hydrolysis", "hydrolyze", "hydrolyzes", "hydrolytic", "cleavage", "cleave", "catalyzes", "水解", "裂解", "催化", "酶", "底物", "反应"],
    "transport": ["transport", "translocation", "localization", "compartment", "uptake", "endocytosis", "internalization", "import", "export", "摄取", "内吞", "转运", "定位"],
    "degradation": ["degradation", "stability", "turnover", "ubiquitin", "降解", "稳定性", "泛素"],
    "perturbation": ["perturbation", "treat", "treated", "treating", "expose", "exposed", "exposing", "stimulate", "stimulated", "administer", "administered", "transfection", "transfected", "transduction", "转染", "转导", "treatment", "exposure", "administration", "stimulation", "处理", "暴露", "knockdown", "knockout", "depletion", "rescue", "mutant", "干预", "敲低", "敲除", "回补", "突变"],
}
KIND_ALIASES = {"expression": "transcription", "rna_regulation": "transcription", "mapping": "orthology", "coactivation": "transcription", "interaction": "binding"}


@lru_cache(maxsize=256)
def _normal(text):
    text = unicodedata.normalize("NFKC", str(text))
    for greek, name in {"α":"alpha", "β":"beta", "γ":"gamma", "δ":"delta", "κ":"kappa", "λ":"lambda", "μ":"mu", "ω":"omega"}.items():
        text = text.replace(greek, name).replace(greek.upper(), name)
    text = re.sub(r"[-‐‑‒–—−]", " ", text)
    text = re.sub(r"\b([A-Z]{2,5})\s+(\d{1,3})\b", r"\1\2", str(text))
    return re.sub(r"\s+", " ", text.casefold().replace("_", " ")).strip()


def _contains(text, phrase):
    phrase = _normal(phrase)
    return bool(phrase) and re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", _normal(text)) is not None


@lru_cache(maxsize=128)
def _group_pattern(phrases):
    alternatives = sorted(set(_normal(x) for x in phrases if _normal(x)), key=lambda x: (-len(x), x))
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(x) for x in alternatives) + r")(?![a-z0-9])") if alternatives else re.compile(r"(?!)")

def _group_contains(text, phrases):
    return _group_pattern(phrases).search(_normal(text)) is not None

def _tokens(text):
    return set(re.findall(r"[a-z0-9][a-z0-9.-]*|[\u4e00-\u9fff]+", _normal(text)))


class MaterialSearch:
    """Read-only task corpus. The caller owns the query/event ledger."""

    def __init__(self, corpus_root):
        self.root = Path(corpus_root).resolve()
        raw = (self.root / "corpus.json").read_bytes()
        self.corpus_sha256 = hashlib.sha256(raw).hexdigest()
        self.corpus = json.loads(raw)
        self.corpus_id = self.corpus["corpus_id"]
        self.materials = {m["material_id"]: m for m in self.corpus["materials"]}
        if len(self.materials) != len(self.corpus["materials"]):
            raise ValueError("duplicate material IDs")
        self.aliases = [set(map(str, group)) for group in self.corpus.get("entity_aliases", [])]

    def _node_terms(self, node):
        terms = {str(node[k]) for k in ("gene", "symbol", "label", "name", "entity") if node.get(k)}
        terms.update(str(x) for x in node.get("aliases", []))
        entity = str(node.get("entity", ""))
        if ":" in entity and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", entity.split(":", 1)[0]):
            terms.add(entity.split(":", 1)[1])
        for key in ("chemical_form", "form"):
            value = node.get("state", {}).get(key)
            if isinstance(value, str):
                terms.add(value)
        originals = {_normal(x) for x in terms}
        for group in self.aliases:
            if originals & {_normal(x) for x in group}:
                terms.update(group)
        return sorted(t for t in terms if len(t) > 1)

    def _validate(self, graph, process, keywords):
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
            raise ValueError("graph.nodes is required; specify your current candidate graph")
        nodes = {n["id"]: n for n in graph["nodes"]}
        if len(nodes) != len(graph["nodes"]):
            raise ValueError("graph has duplicate node IDs")
        if not isinstance(process, dict):
            raise ValueError("process must contain node_ids or relation_id and a kind")
        selected = process.get("node_ids", [])
        if process.get("relation_id"):
            matches = [r for r in graph.get("relations", []) if r.get("id") == process["relation_id"]]
            if len(matches) != 1:
                raise ValueError("process.relation_id must identify a relation in your graph")
            selected = [matches[0]["source"], matches[0]["target"]]
            if matches[0].get("kind") == "dependency":
                reactions = {r["id"]: r for r in graph.get("reactions", [])}
                selected = [n for x in selected for n in ([x] if x in nodes else reactions.get(x, {}).get("inputs", []) + reactions.get(x, {}).get("outputs", []))]
                if not selected:
                    raise ValueError("dependency relation must resolve to graph nodes/reaction participants")
        if not isinstance(selected, list) or not selected or not all(isinstance(x, str) and x in nodes for x in selected):
            raise ValueError("process.node_ids must select existing graph nodes")
        kind = KIND_ALIASES.get(process.get("kind"), process.get("kind"))
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("process.kind is required")
        terms = PROCESS_TERMS.get(kind)
        if terms is None:
            # New processes remain possible without changing a hidden answer key.
            terms = process.get("process_terms")
            if not isinstance(terms, list) or not terms or not all(isinstance(x, str) and len(x.strip()) >= 2 for x in terms):
                raise ValueError("unknown kind: supply explicit process_terms for your proposed biochemical process")
        if isinstance(keywords, str):
            keywords = [keywords]
        if not isinstance(keywords, list) or not keywords or not all(isinstance(x, str) for x in keywords):
            raise ValueError("keywords must be nonempty text or a list of phrases")
        query = " ".join(keywords).strip()
        if not query or len(query) > 2000:
            raise ValueError("query must contain 1..2000 characters")
        groups = [self._node_terms(nodes[x]) for x in dict.fromkeys(selected)]
        missing = [x for x, group in zip(dict.fromkeys(selected), groups) if not any(_contains(query, term) for term in group)]
        if missing:
            raise ValueError("keywords must mention each selected molecular entity (symbol/name/identifier/alias); missing nodes: " + ", ".join(missing))
        if not any(_contains(query, term) for term in terms):
            raise ValueError("keywords must include the specified biochemical process; accepted terms: " + ", ".join(terms))
        return query, groups, terms, {"node_ids": list(dict.fromkeys(selected)), "kind": kind, "relevance_validated": True,
            "biological_truth_checked": False, "rule": "Molecular mentions plus process terms; no gold graph or correctness oracle"}

    def search(self, graph, process, keywords, limit=5, offset=0, filters=None):
        base = {"corpus_id": self.corpus_id, "corpus_sha256": self.corpus_sha256, "tool_version": VERSION,
                "network_used": False, "empty_means": "No matching material in this snapshot; biological status unknown"}
        try:
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
                raise ValueError("limit must be an integer from 1 to 20")
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ValueError("offset must be a nonnegative integer")
            filters = {} if filters is None else filters
            if not isinstance(filters, dict) or set(filters) - {"source", "source_kind", "material_id", "text_all"}:
                raise ValueError("filters supports source (title/URL substring), source_kind (exact or image/pdf/xml/html/json/text artifact MIME family) and material_id (exact), text_all (list of literal body substrings, casefold AND)")
            if any(not isinstance(v, str) or not v.strip() for k, v in filters.items() if k != "text_all"):
                raise ValueError("filter values must be nonempty strings")
            required_text = filters.get("text_all", [])
            if not isinstance(required_text, list) or len(required_text) > 20 or any(not isinstance(x, str) or not x.strip() or len(x) > 200 for x in required_text):
                raise ValueError("filters.text_all must be at most20 nonempty literal body substrings (max200 chars each)")
            query, groups, terms, validation = self._validate(graph, process, keywords)
            validation["keyword_semantics"] = "Selected molecular groups and process are required; remaining keyword tokens rank only. text_all explicitly requires casefold body substrings; source filters title/URL."
            keyword_phrases = [keywords] if isinstance(keywords, str) else keywords
        except (KeyError, TypeError, ValueError) as exc:
            return dict(base, status="invalid_query", error=str(exc), results=[], query_validation={"relevance_validated": False, "biological_truth_checked": False})
        hits = []
        qt = _tokens(query)
        for material in self.materials.values():
            if "material_id" in filters and material.get("material_id") != filters["material_id"]:
                continue
            requested_kind = filters.get("source_kind")
            mime_prefixes = {"image": "image/", "pdf": "application/pdf", "xml": "application/xml", "html": "text/html", "json": "application/json", "text": "text/"}
            if requested_kind and material.get("source_kind") != requested_kind and not (requested_kind in mime_prefixes and any(str(a.get("mime_type", "")).startswith(mime_prefixes[requested_kind]) or requested_kind == "xml" and a.get("mime_type") == "text/xml" for a in material.get("artifacts", []))):
                continue
            if "source" in filters and filters["source"].casefold() not in (material["title"] + " " + material.get("source_url", "")).casefold():
                continue
            chunks = material.get("sections") or [{"id": "full", "title": material["title"], "text": material.get("text", "")}]
            for section in chunks:
                text = section.get("text", "")
                searchable = section.get("title", "") + "\n" + text
                if not all(term.casefold() in text.casefold() for term in required_text):
                    continue
                if not all(_group_contains(searchable, tuple(group)) for group in groups):
                    continue
                if not _group_contains(searchable, tuple(terms)):
                    continue
                overlap = qt & _tokens(searchable)
                score = len(overlap) / max(1, len(qt))
                # Return an original-text window around a molecular mention, never
                # generated conclusions or the human reference chain.
                raw_terms = sorted({t for group in groups for t in group}, key=lambda x: (-len(x), x))
                first_mention = re.search("|".join(re.escape(t) for t in raw_terms), text, re.I) if raw_terms else None
                start = max(0, (first_mention.start() if first_mention else 0) - 160)
                snippet = text[start:start + 1200]
                hits.append({"material_id": material["material_id"], "title": material["title"], "section": section["id"],
                    "section_title": section.get("title", ""),
                    "matched_keywords": [k for k in keyword_phrases if _contains(searchable, k)],
                    "unmatched_keywords": [k for k in keyword_phrases if not _contains(searchable, k)],
                    "matched_required_text": list(required_text),
                    "matched_molecular_terms": [dict(node_id=validation["node_ids"][i], normalized_literal_matches=list(dict.fromkeys(m.group(0) for m in itertools.islice(_group_pattern(tuple(group)).finditer(_normal(searchable)), 12))), matching_scope="normalized section title+body; not original byte offsets") for i, group in enumerate(groups)],
                    "matched_process_terms": list(dict.fromkeys(m.group(0) for m in itertools.islice(_group_pattern(tuple(terms)).finditer(_normal(searchable)), 12))),
                    "alias_matching_is_literal_not_semantic": True,
                    "match_scope": "This original section title+body for relevance/ranking; text_all applies body only; matching is not assay-context verification",
                    "excerpt": snippet, "excerpt_offset": start,
                    "excerpt_truncated": start > 0 or start + len(snippet) < len(text), "source_url": material.get("source_url"),
                    "sha256": hashlib.sha256(text.encode()).hexdigest(), "score": round(score, 6),
                    "source_locator": section.get("source_locator"),
                    "retrieval_scope": section.get("retrieval_scope", "Original material text"),
                    "scope": material.get("scope", "Original source; applicability must be assessed")})
        hits.sort(key=lambda h: (-h["score"], h["material_id"], h["section"]))
        end = min(len(hits), offset + limit)
        return dict(base, status="ok" if hits else "no_match", results=hits[offset:end], total_matches=len(hits),
                    offset=offset, next_offset=end if end < len(hits) else None, has_more=end < len(hits), filters=filters,
                    query_validation=validation, requested_keywords=keywords)

    def read_material(self, material_id, section=None):
        if material_id not in self.materials:
            raise ValueError("Unknown material_id; use graph-grounded search to locate materials")
        material = self.materials[material_id]
        text = material.get("text", "")
        selected_section = {}
        if section is not None:
            matches = [s for s in material.get("sections", []) if s["id"] == section]
            if not matches:
                if section != "full" or material.get("sections"):
                    raise ValueError("Unknown section")
            else:
                selected_section = matches[0]
                text = selected_section["text"]
        artifacts = []
        for resource in material.get("artifacts", []):
            path = (self.root / resource["path"]).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise ValueError("Material resource is not a regular file inside corpus root")
            raw = path.read_bytes()
            if len(raw) != resource["bytes"] or hashlib.sha256(raw).hexdigest() != resource["sha256"]:
                raise ValueError("Material integrity mismatch")
            artifacts.append(dict(resource, path=str(path)))
        return {"material_id": material_id, "title": material["title"], "source_url": material.get("source_url"),
            "source_kind": material.get("source_kind"), "scope": material.get("scope"), "text": text, "section": section,
            "source_locator": selected_section.get("source_locator"),
            "retrieval_scope": selected_section.get("retrieval_scope", "Original material text"),
            "sections": [{"id": s["id"], "title": s.get("title", "")} for s in material.get("sections", [])],
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "artifacts": artifacts,
            "corpus_id": self.corpus_id, "corpus_sha256": self.corpus_sha256, "network_used": False}
