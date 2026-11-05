# Source extraction and version boundaries

Package: `0.1.0`. Tool version: `pertinfer-agent-v0.1.0+e2e-v1.14.1`.

Benchmark dependency: commit
[`55f1e021ed8b7e93356c4dac72202c4678af020c`](https://github.com/DrHuang97/PertInfer-Benchmark/commit/55f1e021ed8b7e93356c4dac72202c4678af020c).
This standalone implementation is currently submitted for review. The explicit
Git pin avoids depending on a changing branch or unpublished package-index release.

## Preserved code

Research tools come from the frozen `e2e-tools-v1.14.1` source snapshot, rather
than the older development-tree copy. Source hashes and the upstream runtime
manifest hash are in `provenance/source_manifest.json`.

- `material_search.py` preserves the frozen bytes.
- `e2e_tools.py` uses the new package version and hashes installed code/instructions
  instead of development-checkout paths.
- Research adapters import shared I/O from the benchmark package.
- The CLI imports installed modules without modifying `sys.path`.
- Original synthetic tool tests and runtime regressions use the new namespace.

Existing lexical retrieval and evidence-tool semantics are preserved. Reference
matching, benchmark graph schema and evaluation rules are not copied here.

## New integration

The runner, command transport, package CLI and method instructions are a new
portable single-task integration layer. They add public input/corpus bindings,
bounded model actions, final graph checks, benchmark export and completed-workspace
sealing. The demo and example command use fictitious sources and identities only.

This is not the historical dynamic experiment dispatcher. Frozen v1.14.1 is not
claimed to have produced earlier model results. Validation covers software
integration, not paper performance or live provider compatibility. A reproduction
release must identify actual models, adapters, prompts, task/material versions,
budgets and run provenance.

The development project and ongoing experiments are preserved. Historical
outputs, local material libraries, credentials and machine-specific paths are
not included. Old experiment seals remain under their original runtime/evaluator.
