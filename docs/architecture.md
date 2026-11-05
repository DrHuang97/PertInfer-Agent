# Method ownership and benchmark integration

PertInfer's scientific decisions are expressed by its instructions and the model's
evolving hypothesis, search, reading and revision actions. Tools provide
observations and provenance; they do not determine biological truth. The scripted
demo backend is only an interface fixture.

| Component | Owner | Responsibility |
|---|---|---|
| Task and graph schema | Benchmark | Shared input/output meaning and validation |
| JSON I/O and canonical hashes | Benchmark | Stable serialization and bindings |
| Public workspace and prediction freezing | Benchmark | Versioned inputs, run plans, output consistency |
| Material packaging | Benchmark | Original files, extracted text and receipts |
| Reference graphs and scoring | Benchmark | Independent evaluation |
| Molecular mapping and human hypotheses | Agent | Species/context reasoning and proposed mechanism |
| Search and reading tools | Agent | Evidence access directed by the current graph/process |
| Prompt and model transport | Agent | Scientific action policy and provider connection |
| Evidence ledger and graph revisions | Agent | Claim support, uncertainty and revision history |
| Budgets and run artifacts | Agent | Execution, observed usage, failures and audit trail |

Shared `schema`, `io` and `freeze` interfaces live in the benchmark package and are
imported at a pinned version. Agent-specific tool receipts and workspace sealing
remain here because they describe method execution rather than benchmark semantics.

## Execution

1. Verify public inputs and the task corpus; create a fresh workspace and record
   release, input, cohort, material, prompt and configuration hashes.
2. Send the public task, animal graph/provenance, instructions and available tools
   to the model. Reference graphs, scores and private release paths are excluded.
3. Dispatch the selected action and return its observation. The model chooses
   subsequent hypotheses, searches and revisions. Tool errors allow query repair.
4. Accept an unmodified saved final graph after structure, evidence-reference and
   benchmark boundary validation. Scientific identities and reactions must be
   supplied by the model. Abstention is explicit.
5. Recheck input bindings, export/freeze predictions through benchmark APIs, and
   hash the completed workspace. Run the independent scorer separately.

The live runner has no evaluator import. Synthetic fixture construction in
`demo.py` and scoring in tests are separate curator/test operations. The benchmark
installs and runs without this package.

## Portable runner scope

One task per workspace, one action per response, and offline evidence retrieval.
The step budget includes both tool and final responses. The command timeout bounds
each model invocation, not the whole experiment. The seed labels the run; the
adapter must apply provider-specific sampling parameters itself.

Historical multi-agent scheduling, provider-specific SDK configurations and
paper-wide orchestration are not migrated. Biological runs require a real model
adapter and an independently released task/material set.

Hashes detect changes relative to recorded bindings; they do not prove chronology
or isolate processes. Access checks and instructions are not an OS sandbox.
Preserve sealed run directories; use a fresh workspace for subsequent exploration.
