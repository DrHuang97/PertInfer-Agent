#!/usr/bin/env python3
"""JSON/JSONL CLI for offline E2ETools. No external model invocation."""
from pathlib import Path
import argparse
import json
import sys
sys.dont_write_bytecode = True
from pertinfer_agent.e2e_tools import E2ETools


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus-root',required=True)
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--online',action='store_true',help='Explicitly permit optional live network adapters; forbidden in offline subject trials.')
    parser.add_argument('--tool',help='Single tool name; arguments read from --args or stdin.')
    parser.add_argument('--args',help='JSON argument object for --tool; otherwise read stdin.')
    ns=parser.parse_args()
    tools=E2ETools(ns.corpus_root,ns.workspace,online=ns.online)
    if ns.tool:
        raw=ns.args if ns.args is not None else sys.stdin.read()
        out=tools.call(ns.tool,raw or '{}')
        print(json.dumps(out,ensure_ascii=False))
        return 0 if out.get('status') not in ('error','invalid_query') else 1
    # One object per line; sequential state persists through workspace files.
    code=0
    for line in sys.stdin:
        if not line.strip():continue
        try:
            req=json.loads(line)
            out=tools.call(req.get('name',req.get('tool')),req.get('arguments',req.get('args',{})))
        except Exception as exc:
            # Log malformed transport requests through the same audited dispatcher.
            out=tools.call('__malformed_request__',{'raw':line.rstrip(),'transport_error':str(exc)})
        print(json.dumps(out,ensure_ascii=False),flush=True)
        if out.get('status') in ('error','invalid_query'):code=1
    return code


if __name__=='__main__':raise SystemExit(main())
