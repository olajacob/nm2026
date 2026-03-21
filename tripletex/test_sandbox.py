#!/usr/bin/env python3
"""Tripletex sandbox smoke tests (no LLM). Run before full /solve runs.

  export TRIPLETEX_SESSION_TOKEN='<base64 session token>'  # Basic password, user 0
  export TRIPLETEX_BASE_URL='https://kkpqfuj-amager.tripletex.dev/v2'   # optional

  python test_sandbox.py              # Tripletex checks only
  python test_sandbox.py --health-url http://127.0.0.1:8080   # + agent GET /health
"""

from __future__ import annotations

import warnings

# Before importing agent → requests → urllib3 (LibreSSL on macOS spams this at urllib3 import).
warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL")

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from agent import TripletexAPI


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL {msg}", file=sys.stderr)


def run_tripletex_checks(api: TripletexAPI) -> int:
    errors = 0
    try:
        who = api.get("/token/session/>whoAmI")
        inner = who.get("value") if isinstance(who, dict) else None
        if isinstance(inner, dict) and inner.get("companyId") is not None:
            _ok(f"whoAmI companyId={inner.get('companyId')} employeeId={inner.get('employeeId')}")
        else:
            _fail(f"whoAmI unexpected shape: {json.dumps(who, ensure_ascii=False)[:300]}")
            errors += 1
    except Exception as e:
        _fail(f"whoAmI: {e}")
        errors += 1

    try:
        acc = api.get("/ledger/account", params={"number": "1920", "fields": "id,number"})
        vals = acc.get("values") if isinstance(acc, dict) else None
        if isinstance(vals, list) and len(vals) >= 1:
            _ok(f"ledger/account 1920 → id={vals[0].get('id')}")
        else:
            _fail(f"ledger/account 1920: {json.dumps(acc, ensure_ascii=False)[:200]}")
            errors += 1
    except Exception as e:
        _fail(f"ledger/account: {e}")
        errors += 1

    try:
        cust = api.get(
            "/customer",
            params={"from": "0", "count": "1", "fields": "id,name,isSupplier"},
        )
        fr = cust.get("fullResultSize")
        _ok(f"customer list (sample) fullResultSize={fr}")
    except Exception as e:
        _fail(f"customer list: {e}")
        errors += 1

    return errors


def run_agent_health(base: str) -> int:
    url = base.rstrip("/") + "/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
        data = json.loads(body)
        if data.get("status") == "ok":
            _ok(f"agent health {url}")
            return 0
        _fail(f"agent health bad body: {body[:200]}")
        return 1
    except urllib.error.URLError as e:
        _fail(f"agent health {url}: {e}")
        return 1
    except Exception as e:
        _fail(f"agent health: {e}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Sandbox checks before NM runs")
    parser.add_argument(
        "--health-url",
        default="",
        help="If set, also GET {url}/health (e.g. http://127.0.0.1:8080)",
    )
    args = parser.parse_args()

    base = os.environ.get(
        "TRIPLETEX_BASE_URL",
        "https://kkpqfuj-amager.tripletex.dev/v2",
    ).rstrip("/")
    token = os.environ.get("TRIPLETEX_SESSION_TOKEN", "").strip()
    if not token or token.startswith("["):
        print(
            "Set TRIPLETEX_SESSION_TOKEN to the full base64 session token (Basic password, user 0).",
            file=sys.stderr,
        )
        return 2

    print("Tripletex sandbox checks…")
    api = TripletexAPI(base, token)
    err = run_tripletex_checks(api)

    if args.health_url.strip():
        print("Agent checks…")
        err += run_agent_health(args.health_url.strip())

    if err:
        print(f"\nFinished with {err} error(s).", file=sys.stderr)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
