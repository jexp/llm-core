"""Auditor CLI: verify the audit hash chain against a running kernel.

Usage: python -m kernel.auditor [--base-url http://localhost:8000]
"""

from __future__ import annotations

import argparse
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify llm-core audit chain")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--key", default="dev-auditor-key")
    args = parser.parse_args()
    response = httpx.get(
        f"{args.base_url}/v1/audit/verify",
        headers={"Authorization": f"Bearer {args.key}"},
        timeout=10,
    )
    response.raise_for_status()
    report = response.json()
    print(report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
