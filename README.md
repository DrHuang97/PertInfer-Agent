# PertInfer Agent

**PertInfer: Agentic Inference of Human Perturbation Chains from Animal Mechanisms**

PertInfer builds human hypothesis graphs from animal mechanisms, selects uncertain
processes for evidence search, reads original materials, and revises its hypotheses
with an explicit evidence ledger. The model chooses the scientific actions; the
runtime preserves graph versions, tool receipts and exported predictions.

The companion [PertInfer Benchmark](https://github.com/DrHuang97/PertInfer-Benchmark)
owns task contracts, material packaging and independent scoring.

This first source release contains research tools extracted from the frozen
**e2e-tools-v1.14.1** snapshot, a new portable model/tool runner, and a synthetic
integration example. It contains no biological dataset or historical experiments,
predictions, logs or result reports. The example uses a scripted fixture, not a
language model. This release has not reproduced the paper's model results.

## Quick start

Requirements: Python 3.10+, Git, and a POSIX host (Linux/macOS). PDF reading and
rendering additionally require Poppler's `pdftotext`, `pdfinfo` and `pdftoppm`.
CI runs on Linux; Windows is not supported by the current file-locking runtime.

During review, the implementation is on `codex/agent-standalone`:

```bash
git clone --branch codex/agent-standalone https://github.com/DrHuang97/PertInfer-Agent.git
cd PertInfer-Agent
python -m venv .venv
source .venv/bin/activate
python -m pip install .
pertinfer-agent demo --output demo-output
```

Installation downloads the benchmark toolkit at the exact commit declared in
`pyproject.toml`. No model SDK or API key is needed for the demo. It runs six
scripted actions: save a hypothesis, search, read, record evidence, save a revision,
and export a final prediction. Choose a fresh output directory for every run.

Score the prediction in a separate benchmark invocation:

```bash
pertinfer-benchmark score --release demo-output/release \
  --predictions demo-output/agent-run/predictions \
  --output artifacts/demo-scores.json
```

The exact fictitious fixture has containment 1. This checks integration and
serialization, not biological performance. Only the scorer receives the private
synthetic release; the runner loads the exported public input directory.

## Running a model

The runner accepts a user-configured executable that reads a JSON request on stdin
and writes one JSON action on stdout. It can call your provider or local model.
There is no default paid model call or bundled provider SDK. See
[model integration](docs/model-integration.md) for the protocol and a complete
command-backed synthetic example.

```bash
pertinfer-agent run --public /path/to/public-inputs --task-id TASK_ID \
  --corpus /path/to/task-corpus --workspace runs/new-run \
  --model-config model-config.local.json --max-steps 40
```

Evidence tools remain offline in this runner. A configured model adapter may
contact its provider. The subprocess is trusted user code, not an OS sandbox.

## Method and tools

- Species-aware identity and correspondence hypotheses, with explicit uncertainty.
- Free hypothesis graphs with immutable revisions and scientific reasons.
- Graph/process-grounded search, filters and pagination over frozen materials.
- Original text, XML, JSON, PDF and image access with provenance checks.
- Evidence records, literal source anchors and explicit support classes.
- A bounded model/tool loop, recorded failures and sealed benchmark predictions.

The [method instructions](src/pertinfer_agent/instructions.md) guide scientific
decisions. The [architecture](docs/architecture.md) describes the repository
boundary; the [tool guide](docs/tools.md) explains reader and evidence limits.

## Outputs

Each run writes registration/input bindings, model request/response records, graph
history, tool audit receipts, an evidence ledger, a final human graph and mapping,
and sealed `predictions/` for the benchmark. `run.json` records status, attempted
model calls and provider-reported usage. Missing usage and cost remain null.
`run_manifest.json` hashes the completed workspace including the final audit receipt.

Generated artifacts are excluded from version control. Failed or incomplete runs
retain diagnostics and export an explicit failure status with an empty final graph.

## Development and provenance

```bash
python -m pip install '.[test]'
python -m pytest -q
```

Tests cover tool regressions, graph/evidence revision, independent scoring, input
mutation, resource limits and command transport. CI also installs a wheel and
runs the demo outside the checkout. See [migration](docs/migration.md) and the
[source manifest](provenance/source_manifest.json) for source hashes and version
boundaries. Historical development seals are not rewritten.

## License

Repository-authored software is released under the [MIT License](LICENSE).
Third-party models, datasets and research materials retain their own terms.
