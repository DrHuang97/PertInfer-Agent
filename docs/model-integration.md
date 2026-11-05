# Model command protocol

The runner starts the configured command once per action. It receives one JSON
object on stdin and must emit one JSON object on stdout. Keep diagnostics on
stderr. Nonzero exit, invalid responses and timeouts become failed runs. Raw
command stderr is not copied into artifacts.

The request contains `protocol: "pertinfer_agent.action_v1"`, `instructions`,
`task`, `source_graph`, `source_provenance`, `tools`, previous action/observation
`history`, `latest_graph_path`, zero-based `step`, and `steps_remaining`.
Tool descriptions are human-readable contracts, not provider-specific schemas.
Full history is resent each step; context reduction is an adapter responsibility.

Return the model's selected tool action:

```json
{"action":{"type":"tool","name":"read_material","arguments":{"material_id":"source-id","offset":0,"max_chars":12000}}}
```

Or a final action naming an actual path returned by `save_graph`:

```json
{"action":{"type":"final","status":"ok","graph_path":"graphs/version-from-save-tool.json","human_mapping":[],"answer":"Supported conclusion and unresolved conditions"}}
```

For abstention use `status: "no_path"` and omit `graph_path`. Optional top-level
`model` and `usage` fields are preserved as provider-reported metadata. Missing
usage stays null; the wrapper does not estimate tokens or prices. Final graph
validation is structural, not scientific correctness. Include an explicit final
action before the step budget expires.

## Configuration

Create an ignored `model-config.local.json`:

```json
{
  "command": ["/absolute/path/to/python", "/absolute/path/to/your_adapter.py"],
  "model": "your-provider/model-version",
  "timeout_seconds": 120
}
```

Pass credentials through the adapter's environment, not arguments or returned
text. Registration records the config hash and model label, not the raw command.
Configure sampling/provider seed in your adapter; `--seed` labels the benchmark
run and does not automatically alter model sampling.

The executable is trusted user code, launched without a shell. It can access its
host filesystem and network. Offline policy applies to evidence tools dispatched
by the runner. Images/PDF pages are returned as verified local paths; an adapter
must explicitly load and transmit them to a vision-capable model to claim viewing.

## Runnable synthetic command example

After the README demo, test the transport without credentials or model calls:

```bash
python - <<'PY'
import json
from pathlib import Path
import sys
config = {"command": [sys.executable, str(Path("examples/model_command.py").resolve())],
          "model": "scripted-synthetic-fixture", "timeout_seconds": 30}
Path("model-config.local.json").write_text(json.dumps(config, indent=2) + "\n")
PY
pertinfer-agent run --public demo-output/public --task-id synthetic_chain \
  --corpus demo-output/corpus --workspace runs/command-demo \
  --model-config model-config.local.json --max-steps 6
pertinfer-benchmark score --release demo-output/release \
  --predictions runs/command-demo/predictions --output artifacts/command-scores.json
```

The included command accepts only the named synthetic task. Replace it with a
provider adapter to run a language model. The fixture is not scientific reasoning.
