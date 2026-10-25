"""Live audit feed: `python -m kernel.tail [--base-url ...] [--key ...] [--count N]`

Follows the kernel's append-only audit chain and pretty-prints new entries as they happen —
the operator's view of every RECEIVED / DECIDED / DECISION / PLAN / OP_RESULT / COMPLETED hop.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

FIELDS = ("tier", "decision", "op", "reason", "backend", "violations")


def _summary(data: dict) -> str:
    bits = [f"{k}={data[k]}" for k in FIELDS if k in data]
    if not bits:
        bits = [json.dumps(data, default=str)[:60]]
    return " ".join(bits)[:80]


def fmt(rec: dict) -> str:
    return (
        f"{rec['seq']:>4}  {rec['ts'][11:19]}  {rec['event']:<10} "
        f"{rec['principal']:<12} {rec['request_id'][:8]}  {_summary(rec['data'])}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Follow the llm-core audit chain")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--key", default="dev-auditor-key")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--count", type=int, default=0, help="exit after N poll cycles (0 = forever)")
    args = parser.parse_args()

    client = httpx.Client(
        base_url=args.base_url, headers={"Authorization": f"Bearer {args.key}"}, timeout=10
    )
    last_seq = 0
    cycles = 0
    try:
        while True:
            response = client.get("/v1/audit")
            response.raise_for_status()
            records = response.json()["records"]
            for rec in records:
                if rec["seq"] > last_seq:
                    print(fmt(rec), flush=True)
                    last_seq = rec["seq"]
            cycles += 1
            if args.count and cycles >= args.count:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    except httpx.HTTPError as e:
        print(f"error talking to kernel at {args.base_url}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
