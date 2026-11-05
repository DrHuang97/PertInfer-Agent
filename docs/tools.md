# Research tools

Explore the audited toolkit in a fresh workspace:

```bash
pertinfer-tools --corpus-root demo-output/corpus --workspace runs/tool-exploration \
  --tool catalog --args '{}'
```

Without `--tool`, send one `{"name":"...","arguments":{...}}` object per stdin
line. The CLI emits one result per line. Additional calls change the audit log,
so use a different workspace from an already sealed run.

| Tools | Purpose |
|---|---|
| `catalog` | Contracts, code hashes and offline policy |
| `save_graph`, `graph_history` | Immutable candidate/revision graphs and reasons |
| `search_materials` | Graph/process-grounded lexical search, filters and pagination |
| `read_material`, `read_text`, `read_xml` | Source metadata and paginated original text |
| `read_json` | JSON pointers and exact filters before pagination |
| `molecular_identity`, `orthology_records`, `drug_rows` | Exact original records; identity is not functional conservation |
| `read_pdf`, `render_pdf_page`, `read_image` | Verified PDFs, extracted text and image paths |
| `add_evidence`, `write_ledger` | Append-only evidence and JSON/CSV export |
| `validate_graph` | Internal structure and evidence references |
| `manifest` | Artifact, corpus and toolkit hashes |

Corpora follow the benchmark's `build-materials` format. Readers verify declared
asset paths, sizes and SHA256. Historical corpora need explicit compatibility
verification and are never rewritten in place by this package.

Search requires a saved graph, a process grounded in it (for example a relation
ID), and molecular/process keywords. This is lexical retrieval, not biological
entailment; a search miss means unknown. Search supports source/material filters
and pagination.

Exploratory graphs may be incomplete. Final export also enforces the benchmark
schema and fixed human input/output boundaries. Evidence checks verify references
and optional literal anchors, not causal truth. Records distinguish direct
support, inference, database annotation and unknown status.

PDF text may misdecode symbols, units or table columns. Render and actually inspect
relevant pages before interpreting quantitative/visual evidence. Images are not
automatically attached to model requests.

## Optional online exploration

Standalone `pertinfer-tools --online` explicitly enables the preserved
`archive_url` and `online_research` adapters. They contact external services and
are unavailable through the offline inference runner. Live database behavior is
not covered by the synthetic validation reported in this release.
