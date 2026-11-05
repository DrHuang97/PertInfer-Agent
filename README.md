# PertInfer: Agentic Inference of Human Perturbation Chains from Animal Mechanisms

Agentic inference of human perturbation chains from animal mechanisms.

Benchmark and evaluator: [PertInfer Benchmark](https://github.com/DrHuang97/PertInfer-Benchmark).

## Status

This repository is initialized for the public method release. Source-code migration
from the development project is pending. This initial commit is not a runnable
agent or a complete paper reproduction release.

## Scope

The agent repository will own:

- Molecular identity and cross-species mapping strategies.
- Human hypothesis-graph generation.
- Graph-directed search and source-reading decisions.
- Evidence-based graph revision and competing explanations.
- Model adapters, prompts, execution controls and run configurations.
- Paper experiment orchestration and result reproduction instructions.

Task formats, benchmark release management and scoring rules are maintained in the
companion benchmark repository. Agent-specific reasoning and tool-use strategies
remain part of this method.

## Integration boundary

The method will use a pinned version of the benchmark's public Python interfaces
to load permitted inputs, validate graphs, export predictions and seal outputs.
The independent evaluator will then score the exported predictions. Benchmark
construction and evaluation must not depend on this agent implementation.

## Release preparation

- Migrate the method implementation and its relevant tests.
- Provide portable model configuration and document required credentials.
- Add a minimal runnable example and resource requirements.
- Document which method version produced each reported experiment.
- Pin the companion benchmark and material-library versions for reproduction.

## License

Repository-authored software is released under the [MIT License](LICENSE).
Third-party models, datasets and research materials retain their own terms.
