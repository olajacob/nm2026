#!/usr/bin/env python3
"""Quick Tripletex connectivity check (no LLM). Uses env:

  TRIPLETEX_BASE_URL   default: https://kkpqfuj-amager.tripletex.dev/v2
  TRIPLETEX_SESSION_TOKEN   required for a real run

For more checks (ledger account, customer list, optional agent /health), see test_sandbox.py.
"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL")

import os
import sys

from agent import TripletexAPI


def main() -> int:
    base = os.environ.get(
        "TRIPLETEX_BASE_URL",
        "https://kkpqfuj-amager.tripletex.dev/v2",
    ).rstrip("/")
    token = os.environ.get("TRIPLETEX_SESSION_TOKEN", "").strip()
    if not token or token.startswith("["):
        print(
            "Set TRIPLETEX_SESSION_TOKEN to your sandbox session token "
            "(Basic auth password, username 0).",
            file=sys.stderr,
        )
        return 2

    api = TripletexAPI(base, token)
    # Lightweight read that exists on all companies
    data = api.get("/token/session/>whoAmI")
    print("OK:", data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
