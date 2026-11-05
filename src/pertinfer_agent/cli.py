"""CLI for a synthetic demo or an explicitly configured command-backed model."""
import argparse
import json


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pertinfer-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="offline scripted synthetic example; no language model")
    demo.add_argument("--output", required=True)
    run = commands.add_parser("run", help="run an explicitly configured model command")
    run.add_argument("--public", required=True)
    run.add_argument("--task-id", required=True)
    run.add_argument("--corpus", required=True)
    run.add_argument("--workspace", required=True)
    run.add_argument("--model-config", required=True)
    run.add_argument("--max-steps", type=int, default=40)
    run.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    if args.command == "demo":
        from .demo import run_demo
        result = run_demo(args.output)
    else:
        from pertinfer_benchmark.io import content_hash, read_json
        from .runner import run_agent
        from .transport import CommandModel
        config = read_json(args.model_config)
        timeout = config.get("timeout_seconds", 120)
        model = CommandModel(config["command"], timeout=timeout)
        # Credentials belong in the adapter's environment, never a recorded config.
        result = run_agent(args.public, args.task_id, args.corpus, args.workspace, model,
                           max_steps=args.max_steps, seed=args.seed,
                           config={"adapter": "command", "model_label": config.get("model"),
                                   "timeout_seconds": timeout, "model_config_hash": content_hash(config)})
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in ("ok", "no_path") else 1


if __name__ == "__main__":
    raise SystemExit(main())
