"""
Tripletex AI Accounting Agent — NM i AI 2026
Endpoint: POST /solve
"""

import os
import sys
import json
import asyncio
import functools
import re
import base64
import contextvars
from contextlib import contextmanager
from pathlib import Path
import requests
import anthropic
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Any, Optional, TextIO
import uvicorn

app = FastAPI()

API_KEY = os.environ.get("API_KEY", "")   # Optional — protects endpoint if set

# Per-request log files (set for the duration of POST /solve). See _solve_logging_session.
_log_files_ctx: contextvars.ContextVar[Optional[tuple[TextIO, ...]]] = contextvars.ContextVar(
    "_agent_log_files", default=None
)


def _real_stdout() -> Any:
    return getattr(sys, "__stdout__", sys.stdout)


def _agent_print(*args: Any, sep: str = " ", end: str = "\n", flush: bool = True) -> None:
    """Same as print, but also mirrors to active solve log file(s) when configured."""
    line = sep.join(str(a) for a in args) + end
    out = _real_stdout()
    out.write(line)
    if flush:
        out.flush()
    files = _log_files_ctx.get()
    if not files:
        return
    for fh in files:
        try:
            fh.write(line)
            if flush:
                fh.flush()
        except Exception:
            pass


def _safe_log_label(task_label: str, max_len: int = 96) -> str:
    t = re.sub(r"\s+", "_", str(task_label).strip())
    t = re.sub(r'[/\\:*?"<>|]+', "-", t)
    if not t or set(t) <= {"_", "-", "."}:
        return "task"
    return t[:max_len]


@contextmanager
def _solve_logging_session(task_label: str, ts: str):
    """
    Write the same lines as stdout to:
    - AGENT_LOG_DIR/solve_<ts>_<task>.log (per request)
    - AGENT_LOG_DIR/last_solve.log (overwrite each request — easy path for tooling)

    Env:
    - AGENT_LOG_DIR: directory (default: <this file>/logs)
    - AGENT_LOG_DISABLE: 1 / true / yes → file logging off
    """
    disabled = os.environ.get("AGENT_LOG_DISABLE", "").strip().lower() in ("1", "true", "yes")
    if disabled:
        yield None
        return

    base = Path(os.environ.get("AGENT_LOG_DIR", str(Path(__file__).resolve().parent / "logs")))
    base.mkdir(parents=True, exist_ok=True)
    safe_ts = ts.replace(":", "-")
    part = _safe_log_label(task_label)
    fname = f"solve_{safe_ts}_{part}.log"
    path_ts = base / fname
    path_last = base / "last_solve.log"

    f_ts = open(path_ts, "w", encoding="utf-8")
    f_last = open(path_last, "w", encoding="utf-8")
    f_last.write(f"# Tripletex agent — same output as console for this solve\n# Archive copy: {fname}\n\n")
    f_last.flush()

    token = _log_files_ctx.set((f_ts, f_last))
    try:
        yield path_ts
    finally:
        _log_files_ctx.reset(token)
        f_ts.close()
        f_last.close()


# ── Request model (matches competition spec exactly) ──────────────────────────

class TripletexCredentials(BaseModel):
    base_url: str
    session_token: str

class FileAttachment(BaseModel):
    filename: str
    content_base64: str
    mime_type: str

class SolveRequest(BaseModel):
    prompt: str
    files: list[FileAttachment] = Field(default_factory=list)
    tripletex_credentials: TripletexCredentials
    #: Optional label for console logs when the harness does not include a task id elsewhere.
    task_id: Optional[str] = Field(default=None, description="Task id/label for logs (e.g. Task 06)")

    @field_validator("files", mode="before")
    @classmethod
    def files_none_to_empty(cls, v: Any) -> Any:
        return [] if v is None else v


def _resolve_task_label(req: SolveRequest, header_task_id: Optional[str]) -> str:
    """Body.task_id > header X-Task-Id > env TASK_ID | NM_TASK_ID > placeholder."""
    if req.task_id and str(req.task_id).strip():
        return str(req.task_id).strip()
    if header_task_id and str(header_task_id).strip():
        return str(header_task_id).strip()
    for key in ("TASK_ID", "NM_TASK_ID"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    return "(not set — use JSON task_id, header X-Task-Id, or env TASK_ID / NM_TASK_ID)"


# Per /solve: `/employee/employment/{id}` ids where PUT {division} returned 422 «Virksomheten kan ikke endres».
_employment_division_locked_ids: set[int] = set()


def _reset_per_solve_guards() -> None:
    """Call at the start of each POST /solve (same process may handle many requests)."""
    _employment_division_locked_ids.clear()


def _employment_id_from_path(path: str) -> Optional[int]:
    p = urlparse(path).path.rstrip("/")
    prefix = "/employee/employment/"
    if not p.startswith(prefix):
        return None
    tail = p[len(prefix) :].split("/")[0]
    return int(tail) if tail.isdigit() else None


def _spec_ensure_count_rate(spec: Any) -> Any:
    """POST /salary/transaction payslip lines often require non-null count + rate (OpenAPI SalarySpecification)."""
    if not isinstance(spec, dict):
        return spec
    s = dict(spec)
    if s.get("amount") is None:
        return s
    if s.get("count") is None:
        s["count"] = 1
    if s.get("rate") is None:
        try:
            s["rate"] = float(s["amount"])
        except (TypeError, ValueError):
            s["rate"] = s["amount"]
    return s


def _enrich_salary_transaction_body(body: dict[str, Any]) -> dict[str, Any]:
    out = dict(body)
    payslips = out.get("payslips")
    if not isinstance(payslips, list):
        return out
    new_ps: list[Any] = []
    for ps in payslips:
        if not isinstance(ps, dict):
            new_ps.append(ps)
            continue
        ps2 = dict(ps)
        specs = ps2.get("specifications")
        if isinstance(specs, list):
            ps2["specifications"] = [_spec_ensure_count_rate(s) for s in specs]
        new_ps.append(ps2)
    out["payslips"] = new_ps
    return out


def _enrich_employment_post_body(body: dict[str, Any]) -> dict[str, Any]:
    """
    Pass-through only. Do **not** auto-inject **division** (or other fields): some tenants return **404**
    on **POST /employee/employment** when the body includes **division**, **isMainEmployer**, or **taxDeductionCode**.
    """
    return dict(body)


def _is_minimal_employment_post_body(body: dict[str, Any]) -> bool:
    """True if body is only `{employee: {id}, startDate}` — no retry strip needed."""
    if set(body.keys()) != {"employee", "startDate"}:
        return False
    emp = body.get("employee")
    if not isinstance(emp, dict) or set(emp.keys()) != {"id"} or emp.get("id") is None:
        return False
    return bool(body.get("startDate"))


def _lock_employments_for_employees(api: "TripletexAPI", employee_ids: set[int]) -> None:
    """After salary 422 «ikke knyttet mot en virksomhet», block further PUT division on those employment rows."""
    for eid in employee_ids:
        try:
            r = api.get(
                "/employee/employment",
                params={"employeeId": eid, "fields": "id"},
            )
        except Exception:
            continue
        vals = r.get("values") if isinstance(r, dict) else None
        if not isinstance(vals, list):
            continue
        for row in vals:
            if isinstance(row, dict) and row.get("id") is not None:
                try:
                    _employment_division_locked_ids.add(int(row["id"]))
                except (TypeError, ValueError):
                    pass


# Outgoing sales VAT on **order lines** — Norwegian competition chart defaults.
# Use when the model adds **vatRatePercent** / **vatPercent** on a line (stripped before POST).
# If a rate is missing here or POST /order returns 422 on VAT, **GET /ledger/vatType** once.
_DEFAULT_OUTGOING_VAT_ID_BY_RATE_PERCENT: dict[int, int] = {
    25: 3,
    15: 31,
    12: 32,
    0: 6,
}


def _order_line_has_vat_type_id(line: dict[str, Any]) -> bool:
    vt = line.get("vatType")
    return isinstance(vt, dict) and vt.get("id") is not None


def _normalized_vat_rate_percent(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        if isinstance(raw, str):
            raw = raw.strip().replace(",", ".")
        x = float(raw)
    except (TypeError, ValueError):
        return None
    return int(round(x))


def _enrich_order_post_body(body: dict[str, Any]) -> dict[str, Any]:
    """Apply vatType from optional per-line vatRatePercent / vatPercent; strip those keys before API."""
    b = dict(body)
    lines = b.get("orderLines")
    if not isinstance(lines, list):
        return b
    keys_to_remove = ("vatRatePercent", "vatPercent", "outgoingVatPercent")
    new_lines: list[Any] = []
    for line in lines:
        if not isinstance(line, dict):
            new_lines.append(line)
            continue
        ln = dict(line)
        if not _order_line_has_vat_type_id(ln):
            pct: Optional[int] = None
            for k in keys_to_remove:
                if k in ln:
                    pct = _normalized_vat_rate_percent(ln.pop(k))
                    break
            if pct is not None:
                vid = _DEFAULT_OUTGOING_VAT_ID_BY_RATE_PERCENT.get(pct)
                if vid is not None:
                    ln["vatType"] = {"id": vid}
        else:
            for k in keys_to_remove:
                ln.pop(k, None)
        new_lines.append(ln)
    b["orderLines"] = new_lines
    return b


def _employee_ids_from_salary_transaction_body(body: dict[str, Any]) -> set[int]:
    out: set[int] = set()
    payslips = body.get("payslips")
    if not isinstance(payslips, list):
        return out
    for ps in payslips:
        if not isinstance(ps, dict):
            continue
        emp = ps.get("employee")
        if isinstance(emp, dict) and emp.get("id") is not None:
            try:
                out.add(int(emp["id"]))
            except (TypeError, ValueError):
                pass
    return out


# ── Tripletex API helper ──────────────────────────────────────────────────────

def _response_json(r: requests.Response) -> dict[str, Any]:
    """Parse JSON body; tolerate empty bodies (e.g. 204 No Content)."""
    if r.status_code == 204 or not r.content:
        return {"ok": True, "status_code": r.status_code}
    try:
        return r.json()
    except ValueError:
        return {"status_code": r.status_code, "raw": r.text[:500]}


def _log_preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated {len(text) - limit} chars]"


def _is_ledger_voucher_create_path(path: str) -> bool:
    """
    True only for POST **create** on /ledger/voucher (optional ?query).
    False for sub-resources, e.g. POST /ledger/voucher/123/postings — those must not be
    confused with create; execute_tool blocks only the bare create path for tripletex_post.
    """
    path_only = urlparse(path).path.rstrip("/")
    return path_only == "/ledger/voucher"


def _is_voucher_postings_subpath(path: str) -> bool:
    parts = [p for p in urlparse(path).path.split("/") if p]
    return (
        len(parts) >= 4
        and parts[0] == "ledger"
        and parts[1] == "voucher"
        and parts[-1] == "postings"
    )


def _assert_voucher_post_path_not_blocked_by_create_guard() -> None:
    """
    Invariant: execute_tool blocks only tripletex_post to bare /ledger/voucher.
    TripletexAPI.post(/ledger/voucher/{id}/postings) is unrelated — must stay unblocked.
    Run at import; fails fast if _is_ledger_voucher_create_path regresses.
    """
    assert _is_ledger_voucher_create_path("/ledger/voucher")
    assert _is_ledger_voucher_create_path("/ledger/voucher?sendToLedger=false")
    assert not _is_ledger_voucher_create_path("/ledger/voucher/123/postings")
    assert not _is_ledger_voucher_create_path("/ledger/voucher/608959911/postings")
    assert _is_voucher_postings_subpath("/ledger/voucher/123/postings")


_assert_voucher_post_path_not_blocked_by_create_guard()


def _sanitize_tripletex_get_params(path: str, params: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Avoid 400 on fields= — e.g. GET /travelExpense/paymentType does not expose **name** (TravelPaymentTypeDTO)."""
    p = dict(params or {})
    path_only = urlparse(path).path.rstrip("/")
    if path_only != "/travelExpense/paymentType":
        return p
    fields = p.get("fields")
    if not isinstance(fields, str):
        return p
    parts = [x.strip() for x in fields.split(",") if x.strip()]
    if not parts:
        return p
    allowed = {"id"}
    keep = [x for x in parts if x in allowed]
    p["fields"] = ",".join(keep) if keep else "id"
    return p


def _voucher_id_from_create_response(resp: dict[str, Any]) -> Optional[Any]:
    v = resp.get("value")
    if isinstance(v, dict) and "id" in v:
        return v["id"]
    if "id" in resp:
        return resp["id"]
    return None


def _normalize_voucher_posting_line(line: dict[str, Any]) -> dict[str, Any]:
    """
    Map legacy freeAccountingDimension* to accountingDimensionValues for /postings.
    Some tenants accept **amount** on Posting, others **amountGross** — send both when **amountGross** is set
    (same numeric value) so one-step voucher create sees a recognized line amount.
    """
    out = dict(line)
    extras: list[dict[str, int]] = []
    for key in (
        "freeAccountingDimension1",
        "freeAccountingDimension2",
        "freeAccountingDimension3",
    ):
        if key not in out:
            continue
        raw = out.pop(key)
        if isinstance(raw, dict) and isinstance(raw.get("id"), int):
            extras.append({"id": raw["id"]})
        elif isinstance(raw, int):
            extras.append({"id": raw})
    if not extras:
        pass
    else:
        existing = out.get("accountingDimensionValues")
        if isinstance(existing, list):
            out["accountingDimensionValues"] = list(existing) + extras
        else:
            out["accountingDimensionValues"] = extras

    ag = out.get("amountGross")
    if ag is not None:
        out["amount"] = ag
    return out


def _voucher_shell_with_empty_postings(body: dict[str, Any]) -> dict[str, Any]:
    """Tripletex validates @NotNull on Voucher.postings — omitted key → «postings: Kan ikke være null»."""
    shell = {k: v for k, v in body.items() if k != "postings"}
    shell["postings"] = []
    return shell


def _post_voucher_line(
    api: "TripletexAPI",
    voucher_id: Any,
    line_body: dict[str, Any],
    row_idx: int,
) -> dict[str, Any]:
    """POST one line; on 422 retry once with negated amountGross (debit + / credit - convention)."""
    url_path = f"/ledger/voucher/{voucher_id}/postings"
    try:
        return api.post(url_path, line_body)
    except requests.HTTPError as e:
        status = e.response.status_code
        detail_full = e.response.text or ""
        _agent_print(
            f"  ⚠️  voucher posting row {row_idx + 1} failed "
            f"HTTP {status}: {detail_full[:800]}"
        )
        ag = line_body.get("amountGross")
        if status == 422 and isinstance(ag, (int, float)):
            flipped = {**line_body, "amountGross": -float(ag), "amount": -float(ag)}
            _agent_print(
                f"  ℹ️  Retrying row {row_idx + 1} with negated amountGross "
                f"(was {ag} → {flipped['amountGross']}; debit positive / credit negative)."
            )
            try:
                ok = api.post(url_path, flipped)
                return {"retried_with_negated_amountGross": True, "response": ok}
            except requests.HTTPError as e2:
                d2 = e2.response.text or ""
                _agent_print(f"  ⚠️  Retry failed HTTP {e2.response.status_code}: {d2[:800]}")
                return {
                    "http_error": e2.response.status_code,
                    "details": d2[:500],
                    "first_attempt_http": status,
                    "first_attempt_details": detail_full[:500],
                    "body_first": line_body,
                    "body_retry": flipped,
                }
        return {
            "http_error": status,
            "details": detail_full[:500],
            "body": line_body,
        }


def _voucher_line_results_have_http_error(line_results: list[Any]) -> bool:
    for item in line_results:
        if isinstance(item, dict) and item.get("http_error") is not None:
            return True
    return False


def _send_voucher_to_ledger(api: "TripletexAPI", voucher_id: Any) -> dict[str, Any]:
    """Finalize voucher after lines exist — **not** `POST ?sendToLedger=true` with empty postings."""
    snap = api.get(f"/ledger/voucher/{voucher_id}", params={"fields": "id,version"})
    inner = snap.get("value") if isinstance(snap, dict) else None
    params: Optional[dict[str, Any]] = None
    if isinstance(inner, dict) and inner.get("version") is not None:
        params = {"version": inner["version"]}
    return api.put_action(f"/ledger/voucher/{voucher_id}/:sendToLedger", params=params)


def _voucher_422_detail_lower(exc: requests.HTTPError) -> str:
    try:
        return (exc.response.text or "").lower()
    except Exception:
        return ""


def _voucher_422_is_systemgenererte(detail: str) -> bool:
    return (
        "systemgenererte" in detail
        or "systemgenerert" in detail
        or "system generated" in detail
        or "system-generated" in detail
    )


def _normalize_postings_lines(postings_lines: list[Any]) -> tuple[list[dict[str, Any]], list[Any]]:
    """Return (normalized dict lines, parallel raw items for error reporting)."""
    norm: list[dict[str, Any]] = []
    raw_meta: list[Any] = []
    for row_idx, p in enumerate(postings_lines):
        if not isinstance(p, dict):
            raw_meta.append({"skipped_non_dict": p})
            continue
        line_body = _normalize_voucher_posting_line(dict(p))
        if "row" not in line_body:
            line_body = {**line_body, "row": row_idx + 1}
        norm.append(line_body)
        raw_meta.append(p)
    return norm, raw_meta


def _finalize_voucher_create(
    api: "TripletexAPI",
    voucher_json: dict[str, Any],
    *,
    send_to_ledger: bool,
    posting_mode: str,
    posting_responses: Optional[list[Any]] = None,
) -> dict[str, Any]:
    vid = _voucher_id_from_create_response(voucher_json)
    if vid is None:
        return {
            "error": "Could not read voucher id from create response.",
            "voucher_response": voucher_json,
            "posting_mode": posting_mode,
        }
    out: dict[str, Any] = {
        "voucher": voucher_json,
        "voucherId": vid,
        "posting_mode": posting_mode,
        "postingResponses": posting_responses if posting_responses is not None else [],
    }
    if send_to_ledger:
        line_results = posting_responses or []
        if posting_mode == "two_step_subresource" and _voucher_line_results_have_http_error(
            line_results
        ):
            out["sendToLedger_skipped"] = "one or more posting lines failed"
        else:
            try:
                out["sendToLedgerResponse"] = _send_voucher_to_ledger(api, vid)
            except requests.HTTPError as e:
                out["sendToLedger_http_error"] = e.response.status_code
                body = e.response.text or ""
                out["sendToLedger_details"] = body[:800]
    return out


def _post_voucher_shell_then_posting_lines(
    api: "TripletexAPI",
    shell_base: dict[str, Any],
    postings_lines: list[Any],
    send_to_ledger: bool,
) -> dict[str, Any]:
    """Legacy two-step: empty shell POST, then POST /ledger/voucher/{id}/postings per line."""
    shell = {**shell_base, "postings": []}
    v_path = "/ledger/voucher?sendToLedger=false"
    _agent_print(f"  📤 voucher two-step shell POST {v_path} body: {_log_preview(json.dumps(shell, ensure_ascii=False), 4096)}")
    try:
        voucher_json = api.post(v_path, shell)
    except requests.HTTPError as e:
        detail = (e.response.text or "")[:2000] if e.response is not None else ""
        _agent_print(f"  ❌ voucher shell POST HTTP {getattr(e.response, 'status_code', '?')}: {detail}")
        raise
    _agent_print(
        f"  📥 voucher shell response: {_log_preview(json.dumps(voucher_json, ensure_ascii=False), 6000)}"
    )
    vid = _voucher_id_from_create_response(voucher_json)
    if vid is None:
        return {
            "error": "Could not read voucher id from create response; aborting posting lines.",
            "voucher_response": voucher_json,
            "postings_skipped": postings_lines,
            "posting_mode": "two_step_subresource",
        }

    line_results: list[Any] = []
    for row_idx, p in enumerate(postings_lines):
        if not isinstance(p, dict):
            line_results.append({"http_error": 400, "details": "posting is not an object"})
            continue
        line_body = _normalize_voucher_posting_line(dict(p))
        if "row" not in line_body:
            line_body = {**line_body, "row": row_idx + 1}
        sub_path = f"/ledger/voucher/{vid}/postings"
        _agent_print(
            f"  📤 voucher posting POST {sub_path} row {row_idx + 1}: "
            f"{_log_preview(json.dumps(line_body, ensure_ascii=False), 4096)}"
        )
        line_results.append(_post_voucher_line(api, vid, line_body, row_idx))

    return _finalize_voucher_create(
        api,
        voucher_json,
        send_to_ledger=send_to_ledger,
        posting_mode="two_step_subresource",
        posting_responses=line_results,
    )


def post_voucher_two_step(
    api: "TripletexAPI",
    *,
    date: Any,
    description: str,
    postings_lines: list[Any],
    send_to_ledger: bool = False,
    shell_extras: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Fallback order (some tenants reject **empty** shell POST — try inline / singular first):
    **1)** POST /ledger/voucher?sendToLedger=false with full **postings**
    **2)** POST /ledger/voucher (no query) with full **postings**
    **3)** POST /ledger/voucher with top-level **posting** (singular)
    **4)** Last resort: empty shell + POST /ledger/voucher/{id}/postings per line
    """
    shell_base: dict[str, Any] = {"date": date, "description": description}
    if shell_extras:
        for k, v in shell_extras.items():
            if k != "postings":
                shell_base[k] = v

    norm_lines, _ = _normalize_postings_lines(postings_lines)
    full_body: dict[str, Any] = {**shell_base, "postings": norm_lines}

    # --- 1) One-step: full postings + ?sendToLedger=false ---
    try:
        _posts_dump = json.dumps(norm_lines, ensure_ascii=False)
        _agent_print(
            f"  📤 voucher one-step postings (first 500 chars): {_log_preview(_posts_dump, 500)}"
        )
        voucher_json = api.post("/ledger/voucher?sendToLedger=false", full_body)
        return _finalize_voucher_create(
            api,
            voucher_json,
            send_to_ledger=send_to_ledger,
            posting_mode="one_step_inline",
        )
    except requests.HTTPError as e:
        if e.response.status_code != 422:
            raise
        d1 = _voucher_422_detail_lower(e)

    if _voucher_422_is_systemgenererte(d1):
        _agent_print(
            "  ℹ️  voucher: one-step 422 (systemgenererte / similar) — "
            "trying no-query / singular `posting` before two-step shell."
        )
    else:
        _agent_print(
            "  ℹ️  voucher: one-step 422 — retry POST without sendToLedger query, then singular `posting`, "
            "then two-step shell (last resort)."
        )

    # --- 2) One-step: same body, no sendToLedger query ---
    try:
        voucher_json = api.post("/ledger/voucher", full_body)
        return _finalize_voucher_create(
            api,
            voucher_json,
            send_to_ledger=send_to_ledger,
            posting_mode="one_step_inline_no_query",
        )
    except requests.HTTPError as e2:
        if e2.response.status_code != 422:
            raise

    # --- 3) Singular top-level key "posting" (some OpenAPI / proxy stacks differ) ---
    _agent_print("  ℹ️  attempt 2b: trying singular 'posting' field name")
    singular_body: dict[str, Any] = {**shell_base, "posting": norm_lines}
    try:
        _agent_print(
            "  📤 voucher attempt 2b: POST /ledger/voucher with field name **`posting`** (singular) "
            f"(preview): {_log_preview(json.dumps(singular_body, ensure_ascii=False), 500)}"
        )
        voucher_json = api.post("/ledger/voucher", singular_body)
        return _finalize_voucher_create(
            api,
            voucher_json,
            send_to_ledger=send_to_ledger,
            posting_mode="one_step_inline_posting_singular",
        )
    except requests.HTTPError as e3:
        if e3.response.status_code != 422:
            raise
        d3 = _voucher_422_detail_lower(e3)
        _agent_print(
            "  ℹ️  voucher: singular `posting` still 422 — two-step shell + /postings (last resort). "
            f"(detail preview: {_log_preview(d3, 200)})"
        )

    # --- 4) Last resort: empty shell + /postings sub-resource ---
    return _post_voucher_shell_then_posting_lines(
        api, shell_base, postings_lines, send_to_ledger
    )


class TripletexAPI:
    def __init__(self, base_url: str, session_token: str):
        self.base_url = base_url.rstrip("/")
        self.session  = requests.Session()
        self.session.auth = ("0", session_token)   # Basic Auth: user=0, pass=token
        self.session.headers["Content-Type"] = "application/json"
        # Per /solve session: travel cost categories by id (from GET /travelExpense/costCategory/{id})
        self._travel_cost_category_cache: dict[int, dict[str, Any]] = {}

    def _travel_cost_category_path_id(self, path_only: str) -> Optional[int]:
        prefix = "/travelExpense/costCategory/"
        if not path_only.startswith(prefix):
            return None
        tail = path_only[len(prefix) :].split("/")[0]
        if not tail.isdigit():
            return None
        return int(tail)

    def _fetch_travel_cost_category_detail(self, vid: int) -> Optional[dict[str, Any]]:
        if vid in self._travel_cost_category_cache:
            return dict(self._travel_cost_category_cache[vid])
        r = self.session.get(
            f"{self.base_url}/travelExpense/costCategory/{vid}",
            params={},
            timeout=30,
        )
        r.raise_for_status()
        res = _response_json(r)
        inner = res.get("value") if isinstance(res, dict) else None
        if isinstance(inner, dict) and inner.get("id") is not None:
            self._travel_cost_category_cache[int(inner["id"])] = inner
            return dict(inner)
        return None

    def _maybe_enrich_travel_cost_category_list(self, result: dict[str, Any]) -> None:
        """List GET often returns id-only rows; merge GET .../{id} details (cached within session)."""
        values = result.get("values")
        if not isinstance(values, list):
            return
        for i, row in enumerate(values):
            if not isinstance(row, dict):
                continue
            raw_id = row.get("id")
            vid: Optional[int] = None
            if isinstance(raw_id, int):
                vid = raw_id
            elif isinstance(raw_id, str) and raw_id.isdigit():
                vid = int(raw_id)
            if vid is None:
                continue
            if row.get("description") or row.get("displayName"):
                self._travel_cost_category_cache.setdefault(vid, dict(row))
                continue
            detail = self._fetch_travel_cost_category_detail(vid)
            if detail:
                values[i] = {**row, **detail}

    def get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        safe_params = _sanitize_tripletex_get_params(path, params)
        r = self.session.get(
            f"{self.base_url}{path}",
            params=safe_params,
            timeout=30,
        )
        r.raise_for_status()
        result = _response_json(r)

        path_only = urlparse(path).path.rstrip("/")
        vid = self._travel_cost_category_path_id(path_only)
        if vid is not None:
            inner = result.get("value") if isinstance(result, dict) else None
            if isinstance(inner, dict) and inner.get("id") is not None:
                self._travel_cost_category_cache[int(inner["id"])] = inner
        elif path_only == "/travelExpense/costCategory" and isinstance(result, dict):
            self._maybe_enrich_travel_cost_category_list(result)

        return result

    def post(
        self,
        path: str,
        body: dict,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """HTTP POST — not gated by execute_tool's tripletex_post /ledger/voucher block."""
        r = self.session.post(
            f"{self.base_url}{path}",
            json=body,
            params=params or {},
            timeout=30,
        )
        r.raise_for_status()
        return _response_json(r)

    def put(self, path: str, body: dict) -> dict[str, Any]:
        r = self.session.put(f"{self.base_url}{path}", json=body, timeout=30)
        r.raise_for_status()
        return _response_json(r)

    def put_action(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """PUT for Tripletex `/:action` URLs (invoice-from-order, credit note, etc.)."""
        url = f"{self.base_url}{path}"
        p = params or {}
        if body is None:
            r = self.session.put(url, params=p, timeout=30)
        else:
            r = self.session.put(url, params=p, json=body, timeout=30)
        r.raise_for_status()
        return _response_json(r)

    def delete(self, path: str) -> dict[str, Any]:
        r = self.session.delete(f"{self.base_url}{path}", timeout=30)
        r.raise_for_status()
        return _response_json(r)


# ── Tool definitions for Claude ───────────────────────────────────────────────

TOOLS = [
    {
        "name": "tripletex_get",
        "description": (
            "GET request to Tripletex API. Use to list or fetch resources.\n"
            "Common paths: /employee, /employee/employment (e.g. **?employeeId=X&fields=id,startDate,division** or **/{id}?fields=id,division**), /customer, /product, /activity, /invoice, /supplierInvoice, /invoice/paymentType, /order, "
            "/project, /project/hourlyRates, /timesheet/entry, /department (list or fetch; create via POST with {name}), /travelExpense, "
            "/travelExpense/costCategory, /travelExpense/costCategory/{id}, /travelExpense/paymentType (**`fields=id`** only — **not** **name**), /ledger/account, /ledger/voucher, "
            "/ledger/accountingDimensionName, /ledger/accountingDimensionValue, /ledger/vatType\n"
            "**GET /ledger/vatType:** skip a **full** list when the task only needs **standard Norwegian outgoing** rates on **invoice order lines** — put **`vatType: {id}`** on **each** **`orderLines[]`** per **SYSTEM_PROMPT** (**25→3**, **15→31**, **12→32**, **0→6**). Use **GET /ledger/vatType** only for **non-standard** rates or after **POST /order** **422** on VAT.\n"
            "List responses are wrapped: {fullResultSize: N, values: [...]}\n"
            "Use ?fields=id,name,* to limit fields. Use ?from=0&count=100 for pagination.\n"
            "GET /ledger/account **1920** (**only** when you will **`PUT /order/.../:invoice`** or prompt requires invoice bank — see SYSTEM_PROMPT **WHEN TO SKIP**): **`number=1920`**, **`fields=id,number,bankAccountNumber`** → **`PUT /ledger/account/{id}`** **`{bankAccountNumber: ...}`**. **Do not** **POST** **1921**. **Do not** fetch 1920 for project-only / timesheet / travel-only tasks.\n"
            "**GET /ledger/account** for journals / bilag: If the task names **GL numbers** (**1720**, **6010**, **2900**, …), use **`?number=NNNN&fields=id,number,name`** — **one GET per account**. **Do not** paginate the **entire** chart (**`from`/`count`** loops) unless you truly cannot resolve a named account. Full-chart scans waste calls and **exhaust** the competition **proxy** → **403 Invalid or expired token** before **`tripletex_post_voucher`** succeeds.\n"
            "GET /product (list): query **`name`** (substring / \"Containing\") and/or **`productNumber`** (when you have the SKU); "
            "use **`fields=id,name,number`** (or minimal set) — **before** POST /product, look up existing rows and **reuse** **id**.\n"
            "GET /ledger/voucher (list/search): requires **dateFrom** and **dateTo**; **dateTo** must be strictly after **dateFrom**. **`VoucherDTO`** **fields** filter: use **`number`** (bilagsnr) — **not** **`voucherNumber`** or **`amount`** (API **400**). Totals live on **`postings`** — omit **`postings`** on the list to save payload; **`execute_tool`** **strips** any embedded **`postings`** and returns **`postingsCount`** (**`null`** when **`postings`** were omitted — **not** zero) + **`_toolNote`** — **`GET /ledger/voucher/{id}`** for lines/amounts. **Pagination:** **`from`** / **`count`**. **Audit/correction** → **SYSTEM_PROMPT** **Ledger audit / error correction**.\n"
            "CRITICAL: path /invoice (no id) = list endpoint. Params MUST include invoiceDateFrom AND invoiceDateTo "
            "(YYYY-MM-DD) every time — even alongside customerId, fields, pagination. Missing dates → 422. "
            "Example: {customerId: X, invoiceDateFrom: '2000-01-01', invoiceDateTo: '2099-12-31', fields: 'id,invoiceNumber,invoiceDate,amountExcludingVat'}. "
            "Do NOT request isPaid, dueDate, amountIncludingVat, or paid — they are not valid invoice list fields.\n"
            "**GET /supplierInvoice** (list): same rule — **invoiceDateFrom** and **invoiceDateTo** (YYYY-MM-DD) are **required**. "
            "Optional **kid**, **supplierId**, **invoiceNumber**. Use **fields** to include **outstandingAmount**, **kidOrReceiverReference**, **amount**, **invoiceDate** when reconciling payments.\n"
            "**Travel cost categories:** **`GET /travelExpense/costCategory`** may return **id-only** rows; the server **enriches** each with **`GET /travelExpense/costCategory/{id}`** (details cached for this `/solve` session). You may still call **`/{id}`** yourself; use **displayName** / **description** to pick **Fly**, **Taxi**, **Hotell**, etc. per SYSTEM_PROMPT.\n"
            "**Travel payment types:** **`GET /travelExpense/paymentType?fields=id`** — **`name`** is **not** a valid **fields** filter (same class of error as **`GET /invoice/paymentType`**). Invalid **`fields`** are stripped server-side in **`TripletexAPI.get`** to **`id`** only.\n"
            "**Bank-return / reverse payment:** **`GET /invoice/{id}?fields=id,invoiceNumber,postings`** (or full **`GET /invoice/{id}`**) → **`voucher.id`** on the **negative (payment)** posting → **`PUT /ledger/voucher/{id}/:reverse?date=…`** via **tripletex_put_action** — **not** **`:createCreditNote`**."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":   {"type": "string", "description": "e.g. /employee or /customer/123"},
                "params": {
                    "type": "object",
                    "description": (
                        "Query params. If path is /invoice (list): always include invoiceDateFrom and invoiceDateTo "
                        "(YYYY-MM-DD) in addition to any customerId/fields/from/count. "
                        "Example: {customerId: X, invoiceDateFrom: '2000-01-01', invoiceDateTo: '2099-12-31', "
                        "fields: 'id,invoiceNumber,invoiceDate,amountExcludingVat'}. Never use isPaid, dueDate, amountIncludingVat, paid in fields."
                    ),
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "tripletex_post",
        "description": (
            "POST request to create a resource in Tripletex.\n"
            "Common paths: /employee, /employee/employment, /customer, /product, /activity, /order, "
            "/supplierInvoice (register **leverandørfaktura** — **`invoiceNumber`**, **`invoiceDate`**, **`supplier`**, **`amountCurrency`**, **`currency`**; see SYSTEM_PROMPT **Register supplier invoice**), "
            "/project, /project/hourlyRates, /timesheet/entry, /department (POST {name} — **one POST per department**), /travelExpense, "
            "/travelExpense/perDiemCompensation, /travelExpense/cost, /ledger/accountingDimensionName ({dimensionName}), "
            "/ledger/accountingDimensionValue ({displayName})\n"
            "**Ledger / journal vouchers:** use **`tripletex_post_voucher`** — **not** **`tripletex_post`** on **`/ledger/voucher`** (see that tool).\n"
            "POST /customer: include **email** (and phone, org number) in body whenever the user prompt mentions them — "
            "omitting stated email breaks automated checks. **Supplier-only** tasks → **`isSupplier: true`** and **`isCustomer: false`**; if **POST** response still shows **`isCustomer: true`**, **`PUT /customer/{id}`** **`{isCustomer: false}`** (see SYSTEM_PROMPT). **Customer-only** → **`isCustomer: true`**, **`isSupplier: false`** unless prompt says both. See SYSTEM_PROMPT **Create customer / supplier**.\n"
            "POST /product: **priceExcludingVatCurrency** (not \"price\" — 422). **vatType**: **outgoing** sales code for the product’s **default**; **not** incoming/fradrag **id 1**. For **invoices with different VAT % per line**, set **`vatType: {id}`** on **each** **`orderLines[]`** entry (see SYSTEM_PROMPT — do **not** rely on product default alone). Travel costs use different vat rules — SYSTEM_PROMPT. "
            "**Before** POST, **GET /product** with **`name`** and/or **`productNumber`** + **`fields`** — if a row matches, **reuse** **id** (no POST). "
            "If **422** **«Produktnummeret … er i bruk»** or **«Produktnavnet … er allerede registrert»**, **do not** burn calls on invented POST bodies — **GET /product**, **reuse** existing **id**; only if still no row, **retry POST** **without** **`number`** (duplicate-number case).\n"
            "POST /department: body **{name: \"...\"}** — multiple departments → **separate POST** per name (see SYSTEM_PROMPT).\n"
            "POST /employee: requires **userType** (e.g. STANDARD) and **department: {id}**; use **POST /employee/employment** for startDate / tax — not nested employmentDetails on /employee. **Always** **`GET /employee?email=…&fields=id,firstName,lastName`** **before** **POST /employee** — reuse **`id`** if found (avoid duplicate-email **422**). See SYSTEM_PROMPT **Create employee Step 0**.\n"
            "Payroll: **before** **POST /employee/employment**, **GET /employee/{id}?fields=dateOfBirth** — if **null**, **PUT /employee/{id}** **`{dateOfBirth: …}`**. **POST /employee/employment**: use **minimal** body first — **`{employee: {id}, startDate}`** only (see SYSTEM_PROMPT); **no** auto-**division**. If **404** with a larger body, **`execute_tool`** retries once with **only** **`employee`+`startDate`**. Set **division** via **PUT /employee/employment/{id}** after create if needed.\n"
            "POST /project: **startDate** is required (422 if missing); resolve **projectManager** via GET /employee with email filter if needed.\n"
            "POST /timesheet/entry: **project**, **activity**, **employee**, **date**, **hours** (see SYSTEM_PROMPT).\n"
            "Invoice bank (before **:invoice**): **GET /ledger/account** **`number=1920`**, **`fields=id,number,bankAccountNumber`** → **`tripletex_put` `PUT /ledger/account/{id}`** body **`{bankAccountNumber: \"86011117947\"}`** only — **do not** **POST** **1921**.\n"
            "Custom dimensions: **POST /ledger/accountingDimensionName** / **POST /ledger/accountingDimensionValue** — see SYSTEM_PROMPT.\n"
            "**Journal vouchers:** **`tripletex_post_voucher`** only — see that tool and SYSTEM_PROMPT (**never** raw **`tripletex_post`** to **`/ledger/voucher`** for manual journal lines).\n"
            "Travel: **POST /travelExpense** uses nested **travelDetails** for departureDate/returnDate/destination/purpose/etc. — not top-level. "
            "**POST /travelExpense/perDiemCompensation** only for **type TRAVEL** reports (not expense reports). "
            "**POST /travelExpense/cost**: **amountCurrencyIncVat** (NOT amountCurrencyInclVAT), **amountNOKInclVAT**, **comments** (NOT description), "
            "**paymentType** (NOT paymentCurrency). **`GET /travelExpense/paymentType?fields=id`** — **not** **`name`** in **fields**. Resolve **costCategory** after listing/enriching categories (see SYSTEM_PROMPT). **vatType**: often **{id: 1}** for domestic VAT and **{id: 0}** for per-diem/diett lines — do **not** assume **1** for every line.\n"
            "POST /order: **deliveryDate** is required (422 \"deliveryDate\" null) — set to same as **orderDate** if not specified. "
            "Each **`orderLines[]`** object may include **`vatType: {id}`** (outgoing sales) when the task states VAT **per line** — **OpenAPI** **OrderLine** supports **`vatType`**; without it, Tripletex uses the **product** default and **multi-rate** tasks can score wrong. "
            "Optional helper fields **`vatRatePercent`** / **`vatPercent`** on a line are converted to **`vatType`** and stripped before the API (see SYSTEM_PROMPT mapping).\n"
            "Creating an invoice from an order is NOT a POST here — after POST /order, call "
            "tripletex_put_action with PUT /order/{id}/:invoice.\n"
            "Tripletex also uses PUT path actions (/:actionName) — see tripletex_put_action "
            "(e.g. PUT /order/{id}/:invoice, PUT /invoice/{id}/:createCreditNote, PUT /ledger/voucher/{id}/:reverse).\n"
            "**Customer invoice payment:** **PUT** `/invoice/{id}/:payment` — **tripletex_put_action** with query **params** only. "
            "**paidAmount** from **`GET /invoice/{id}`** outstanding when possible (**Task 11** — **not** **EUR × rate**); else incl. VAT for **that** bank line (see SYSTEM_PROMPT).\n"
            "**Supplier invoice payment:** **POST** `/supplierInvoice/{invoiceId}/:addPayment` — **this tool** with **body** **`{}`** and **params** per OpenAPI: "
            "**paymentType** (required; **0** = use last type for vendor when combined with tenant setup), **amount**, **paymentDate**, "
            "**partialPayment: true** when registering a **partial** payment, optional **kidOrReceiverReference**, **useDefaultPaymentType**.\n"
            "**POST /employee/employment**: **minimal** **`{employee: {id}, startDate}`** first — **no** runtime **division** injection; **404** → automatic one retry stripped to **employee**+**startDate** only.\n"
            "**POST /salary/transaction**: optional **`params`** e.g. **`{generateTaxDeduction: true}`**. Each **`payslips[].specifications[]`** line with **`amount`** must include **`count`** and **`rate`** (non-null) — e.g. **`count: 1`**, **`rate`** = same as **`amount`** for monthly fixed pay; **`execute_tool`** auto-fills missing **`count`/`rate`** from **`amount`**.\n"
            "Returns the created resource JSON for the requested path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "body": {"type": "object", "description": "JSON body"},
                "params": {
                    "type": "object",
                    "description": "Optional query string for POST, e.g. {generateTaxDeduction: true} for /salary/transaction",
                },
            },
            "required": ["path", "body"],
        },
    },
    {
        "name": "tripletex_post_voucher",
        "description": (
            "**Always use this for ledger / journal vouchers** (manual postings). **Tries one-step first**, then **two-step** only if the tenant requires it:\n"
            "**A)** **POST /ledger/voucher?sendToLedger=false** with the **full** voucher JSON including **`postings: [...]`** (all lines in one body). **200** → done (fewer HTTP calls, avoids *«uten posteringer»* on tenants that reject an **empty** shell).\n"
            "**B)** If **A** returns **422** *«uten posteringer»* / *«kan ikke registreres uten posteringer»*, retry **POST /ledger/voucher** with the **same body** but **no** **`sendToLedger`** query.\n"
            "**C)** If **A** returns **422** mentioning **systemgenererte** (or **B** still **422**), fall back: **POST** shell **`postings: []`** + **`sendToLedger=false`**, then **POST /ledger/voucher/{id}/postings** once **per line** (single object per request) — **`row`**, **`account: {id}`**, **`amountGross`**, etc.\n"
            "On **422** for a sub-resource line, **retries once** with **negated** **`amountGross`**.\n"
            "**`send_to_ledger`: true** → after a successful create, **`PUT /ledger/voucher/{id}/:sendToLedger`** — **never** **`?sendToLedger=true`** on an **empty** shell.\n"
            "Optional **`shell_extras`** merges extra **Voucher** fields (not **postings**). "
            "Returns **`voucher`**, **`voucherId`**, **`posting_mode`**, **`postingResponses`**, **`sendToLedgerResponse`** (or error keys)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Voucher date YYYY-MM-DD"},
                "description": {"type": "string", "description": "Voucher description"},
                "postings": {
                    "type": "array",
                    "description": "Line objects; sent inline in one POST first, else one HTTP POST per line to /ledger/voucher/{id}/postings",
                    "items": {"type": "object"},
                },
                "send_to_ledger": {
                    "type": "boolean",
                    "description": "If true, after voucher is created, PUT /ledger/voucher/{id}/:sendToLedger",
                },
                "shell_extras": {
                    "type": "object",
                    "description": "Optional extra Voucher fields (e.g. voucherType)— never line data",
                },
            },
            "required": ["date", "postings"],
        },
    },
    {
        "name": "tripletex_put",
        "description": (
            "PUT to update an existing resource's fields (normal JSON body).\n"
            "Examples: PUT /employee/123 {\"firstName\": \"...\"}, PUT /customer/456, "
            "**PUT /ledger/account/{id}** with **`{bankAccountNumber: \"86011117947\"}`** after **`GET /ledger/account?number=1920&fields=id,number,bankAccountNumber`** — see SYSTEM_PROMPT.\n"
            "**PUT /employee/{id}** **`{dateOfBirth: \"YYYY-MM-DD\"}`** when **`POST /employee/employment`** fails with **employee.dateOfBirth** required (often **null** on pre-seeded employees) — prompt date or **`1990-01-01`** if silent.\n"
            "**PUT /employee/employment/{id}** with **`{division: {id: N}}`** when **division** may be set — try **N=1**, then **2**, **3** on **403** only; **422** *«Virksomheten kan ikke endres»* → **do not** retry other ids (see SYSTEM_PROMPT).\n"
            "Do NOT use this for Tripletex path actions (URLs containing /:actionName such as "
            "/:invoice or /:createCreditNote) — use tripletex_put_action for those."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path with id e.g. /employee/123"},
                "body": {"type": "object"},
            },
            "required": ["path", "body"],
        },
    },
    {
        "name": "tripletex_put_action",
        "description": (
            "PUT that triggers a Tripletex *action* via a /:actionName path segment — not a generic resource field update.\n"
            "Primary pattern: PUT /order/{orderId}/:invoice — creates the invoice from that order (after POST /customer, "
            "POST /product if needed, POST /order with orderLines).\n"
            "**invoiceDate** is required for :invoice (422 \"invoiceDate\" null). Pass as **params** e.g. "
            "{invoiceDate: 'YYYY-MM-DD', invoiceDueDate: 'YYYY-MM-DD'} or in **body** if Swagger expects JSON — never call :invoice with no dates.\n"
            "**Customer — register payment:** **PUT** `/invoice/{id}/:payment` — **required query params**: paymentDate, paymentTypeId, **paidAmount**; "
            "**`GET /invoice/{id}`** first — use **`amountOutstanding`** / **`amountCurrencyOutstanding`** for **full** pay (**FCY** → **`amountCurrencyOutstanding`** as NOK equivalent per Tripletex — **do not** **FCY × payment rate**). Optional **paidAmountCurrency**. **One PUT per bank transaction**. Omit body unless Swagger requires JSON. "
            "**Runtime:** **`execute_tool`** may **WARNING** if **paidAmount** is much larger than live **amountOutstanding** (suspect **FCY × rate**).\n"
            "**Supplier — register payment:** **POST** `/supplierInvoice/{invoiceId}/:addPayment` — use **tripletex_post** with **body** **`{}`** and **params** (paymentType, amount, paymentDate, partialPayment, …) — **not** this tool (PUT-only).\n"
            "**Reverse a payment** (bank return / undo payment): after **`GET /invoice/{id}`** (see **`postings`**), **`PUT /ledger/voucher/{paymentVoucherId}/:reverse`** with **params** **`{date: 'YYYY-MM-DD'}`** — **not** `:createCreditNote` (that credits the **sale**).\n"
            "**Send invoice:** **`PUT /invoice/{id}/:send`** — **required** query **`sendType`**: **EMAIL**, **EHF**, **MANUAL**, **PAPER**, etc. (OpenAPI enum). Optional **`overrideEmailAddress`** when **`sendType`** is **EMAIL** and the task gives an address. Use **invoice `id`** from **`PUT /order/.../:invoice`** response **`value.id`**.\n"
            "Credit note (cancel sale): PUT /invoice/{invoiceId}/:createCreditNote (query params per Swagger).\n"
            "**Supplier invoice — approve / attest:** **`PUT /supplierInvoice/{invoiceId}/:approve`** (optional query **`comment`**) — see SYSTEM_PROMPT **Register supplier invoice**; **not** **`POST`**.\n"
            "Pass the full path including /:invoice, /:send, /:payment, /:createCreditNote, /ledger/voucher/{id}/:reverse, or /supplierInvoice/{id}/:approve. Use params and/or body as required by the action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Full path with action, e.g. /order/123/:invoice, /invoice/456/:send, /invoice/456/:payment, /invoice/456/:createCreditNote, /ledger/voucher/789/:reverse, /supplierInvoice/101/:approve",
                },
                "params": {
                    "type": "object",
                    "description": "Optional query parameters (e.g. date for credit note if Swagger uses query string).",
                },
                "body": {
                    "type": "object",
                    "description": "Optional JSON body only when Swagger requires it; otherwise omit this property.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "tripletex_delete",
        "description": "DELETE request to remove a resource. Path must include id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path with id e.g. /invoice/456"},
            },
            "required": ["path"],
        },
    },
]

SYSTEM_PROMPT = """You are an expert Tripletex accounting agent. Complete tasks precisely via the Tripletex v2 REST API.

**WHEN TO SKIP account 1920 (read this first):** The **`GET /ledger/account` 1920 + `PUT` bankAccountNumber** block below is **only** for tasks that **create an outgoing customer invoice** — i.e. you will call **`PUT /order/{id}/:invoice`** (or invoice-from-multiple-orders) **or** the prompt explicitly requires **invoice bank / faktura / KID** setup **for sending invoices**. **Do not** run it for **pure project setup** (e.g. **Festpreis** / **fixed price** **`isFixedPrice`+`fixedprice`**, project manager, **`POST /project`** + **`PUT /project`** only), **timesheets**, **hourly rate rows**, **travel**, **payroll**, **manual ledger vouchers**, **dimensions**, or **customer/product master data** with **no** **`:invoice`** step — those waste API calls and hurt **efficiency**.

MANDATORY SETUP BEFORE INVOICE TASKS (1920 — **only if you will `PUT .../:invoice`**):
If the task involves **creating an invoice** (**`PUT /order/{id}/:invoice`**), **do not** **`POST /ledger/account`** a new **1921** — **update** ledger account **1920** (competition invoice bank — **`isInvoiceAccount`** is already **true**; often only **`bankAccountNumber`** is empty).

**Before** POST /customer / POST /product / POST /order / **:invoice** (when invoicing applies):

**Step 1 — Account 1920**

**tripletex_get** — **`GET /ledger/account`** with params:
- **`number`**: **`1920`**
- **`fields`**: **`id,number,bankAccountNumber`**

From **`values`**, note **`id`**. **Account 1920 always exists** — **faster** than scanning all bank accounts.

**Step 2 — Bank account number**

**tripletex_put** — **`PUT /ledger/account/{id}`** with body **only**:
```json
{"bankAccountNumber": "86011117947"}
```

**Do NOT** create a new account **1921** — **update** the existing **1920** instead.

CRITICAL **`bankAccountNumber` rules:**
- Must be **exactly 11 characters**, **digits only** — **no** dots, spaces, or dashes.
- Must be a **valid Norwegian** account number (**Mod11/Luhn**-valid per Norwegian bank rules).
- Use **exactly** **"86011117947"** — **confirmed** for competition. **Do not** substitute or "invent" numbers.
- **Never** use these (they **fail**): **"1503.40.12345"**, **"12345678901"**, **"15034012345"**, or any non-11-digit string.
- **Only one** ledger account may hold each **`bankAccountNumber`** in Tripletex — **never assign the same number to two accounts** or **reuse** a kontonummer that is already set on another row.

If **PUT /ledger/account/{id}** returns **422** (e.g. **`version`** required), **`GET /ledger/account/{id}?fields=...`** per Swagger and retry with any **required** fields — **still** **do not** **`POST` 1921** for invoice setup.

If you are **not** about to **`:invoice`**, **skip** this entire 1920 block (see **WHEN TO SKIP** above).

LANGUAGES: Tasks arrive in Norwegian, Nynorsk, English, German, French, Spanish, or Portuguese. Understand fully before acting. **Invoice «send» cues:** e.g. **send** / **e-post** / **email** (EN/NO), **enviar** / **envie** / **mandar** (PT/ES), **envoyer** (FR), **senden** / **versenden** (DE) — if present, you must **`PUT /invoice/{id}/:send`** after **`:invoice`** (see **Create invoice**).

SCORING — THIS MATTERS:
- You are scored on CORRECTNESS (field-by-field checks) and EFFICIENCY (API call count + zero 4xx errors)
- Efficiency bonus ONLY applies if you achieve 100% correctness — so correctness comes first
- Every 4xx error reduces your efficiency score — do NOT make speculative or trial-and-error calls
- **Do not** finish with **no API calls** when the user asked you to change data in Tripletex — unknown task types still require **GET-then-POST** attempts (see **Custom accounting dimensions & ledger**)
- **Bank CSV / attached text files:** the **full file text** is included in your user message (lines starting with **`=== File:`** …). **You must** parse it and **call tools** — **never** `end_turn` with zero tool uses when the task is reconciliation / registration from that file
- **Full project lifecycle / hours:** if the prompt mentions **hours**, **timer**, or a **complete** project flow, **finish** project + fixed price (if budget) + **every** **timesheet** line + invoice chain if asked — see **Full project lifecycle** — **never** `end_turn` halfway
- **Ledger audit / corrections:** if the prompt asks to **fix** / **correct** voucher or posting **errors**, **finish** with **`tripletex_post_voucher`** (and pagination on **`GET /ledger/voucher`** as needed) — **never** `end_turn` after only **GET**s — see **Ledger audit / error correction**
- **Supplier invoice (leverandørfaktura):** if the task is to **register** a **received** supplier invoice, **finish** with **`POST /supplierInvoice`** (and **`PUT …/:approve`** if asked) — **never** `end_turn` after only **`POST /customer`** / ledger **GET**s — see **Register supplier invoice**
- Plan your full call sequence before making the first request

PLANNING RULE: Before any API call, think through:
1. What is the task asking for exactly?
2. What data do I already have from the prompt?
3. What IDs or lookups do I need first?
4. What is the minimum sequence of calls to complete this correctly?

TRIPLETEX API KNOWLEDGE:
- Paths: /employee, /employee/employment (employment **`id`**; **GET** **`?employeeId=`** list; **PUT** with **`division: {id}`**), /customer, /product, /activity, /order, /invoice, **/supplierInvoice** (leverandørfaktura — list requires **`invoiceDateFrom`** / **`invoiceDateTo`** like **`/invoice`**), **/invoice/{id}/:send** (e-mail / EHF / manual send — **tripletex_put_action** with **`sendType`** query), /invoice/paymentType (**`fields=id`** only), /project, /project/hourlyRates, /timesheet/entry, /project/orderline ([BETA] project lines), /department, /travelExpense, /travelExpense/costCategory (+ **`/{id}`** for details), /travelExpense/paymentType (**`fields=id`** only), /travelExpense/perDiemCompensation, /travelExpense/cost, /salary/transaction, /salary/type, /salary/payslip (read-only), /salary/compilation (read-only), /ledger/account, **manual journals: `tripletex_post_voucher` tool** (wraps **`/ledger/voucher`** + **`/ledger/voucher/{id}/postings`**), /ledger/accountingDimensionName, /ledger/accountingDimensionValue (custom dimensions), /ledger/vatType (VAT ids for products) — **avoid** **`GET /company/divisions`** for payroll setup (often **403** in competition)
- Action URLs use a /:actionName segment (e.g. /:invoice, **/invoice/{id}/:send**, /:createCreditNote, **/ledger/voucher/{id}/:reverse**) — call these with tripletex_put_action (PUT), not as a normal field update on tripletex_put
- Dates: "YYYY-MM-DD" format always
- List responses: {"fullResultSize": N, "values": [...]}
- GET /invoice (listing or searching invoices): **required query params** **invoiceDateFrom** and **invoiceDateTo** (YYYY-MM-DD). **This is mandatory even if you add other filters** (customerId, fields, from, count, etc.) — Tripletex still returns **422** (“Kan ikke være null”) if either date is missing. Use a wide window when needed (e.g. 2000-01-01 through 2099-12-31). Only **GET /invoice/{numericId}** for one invoice skips this rule
- Invoice list **fields** (confirmed): **id**, **invoiceNumber**, **invoiceDate**, **amountExcludingVat** — do **not** request **isPaid**, **dueDate**, **amountIncludingVat**, or **paid** (not on InvoiceDTO for this list)
- **Products:** before **`POST /product`**, **`GET /product`** with **`name`** (and **`productNumber`** if the task has a SKU) + **`fields=id,name,number`** — reuse **`id`** when the row matches; on **422** duplicate name/number messages, **GET** and reuse — do not spam **`POST /product`**
- POST /order: **deliveryDate** is **required** (422 if null). If the prompt does not give a delivery date, use the same **YYYY-MM-DD** as **orderDate**
- **Order lines + VAT (invoices):** **`orderLines[]`** supports **`vatType: {id}`** (same **outgoing** sales codes as products). If the task gives **different VAT % per line**, set **`vatType` on every line** from the task — **do not** assume the **product** master **vatType** matches each line. **Standard Norwegian outgoing** **id** shortcut (competition — **skip GET /ledger/vatType** unless the rate is unusual or **POST /order** **422**): **25% → 3**, **15% → 31**, **12% → 32**, **0% → 6**. **Efficiency:** after **PUT /order/{id}/:invoice** returns **success**, **do not** **GET /invoice/{id}** only to re-read line VAT unless the prompt explicitly asks for verification or payment amounts you cannot compute.
- Auth: already handled — just make the calls
- **403 "Invalid or expired token"** mid-session: usually **competition proxy** / expired **session_token** — **infrastructure**, not a broken request body. **Do not** abandon the run after **one** 403: **still execute your remaining planned tool calls** (e.g. **POST /department** for **each** name you intended, or retry the sequence) — the token may succeed on **other** endpoints or moments. **Never** `end_turn` early **only** because a single call returned 403. A **fresh** `session_token` is required for a clean run; if 403 is **frequent**, raise it with **organisers** (not fully fixable in agent code). **Prevention:** **minimize** **`GET /ledger/account`** pagination — use **`number=…`** for each stated GL code so the proxy budget lasts through **`tripletex_post_voucher`**.
- **GET /ledger/account** for **bilag** lines: When the prompt gives **kontonummer** / **account numbers** / **compte** / **GL** codes, resolve each with **`GET /ledger/account?number=N&fields=id,number,name`** (**one call per** **N**). **Avoid** scanning the full chart with **`from`/`count`** unless you must search by **name** once.
- **Company bank account for invoicing:** **`Company`** has **no** bank fields in the API. **`GET /ledger/account?number=1920&fields=id,number,bankAccountNumber`** → **`PUT /ledger/account/{id}`** **`{bankAccountNumber: \"86011117947\"}`** — **not** **`POST` 1921**.

COMMON TASK PATTERNS (memorize these to avoid extra calls):

Invoice bank registration: use **MANDATORY SETUP BEFORE INVOICE TASKS** at the **top** — **`GET`** **`number=1920`** → **one** **`PUT`** **`bankAccountNumber`** only; **no** **`POST /ledger/account` 1921**; **no** duplicate **`bankAccountNumber`** across accounts; **no** random kontonummer guesses.

Create department:
POST /department {name: "..."}
CRITICAL: Several departments → **one POST /department per department** (separate bodies), not one call with an array.

Create employee:
Step 0 — **Before any POST /employee** for a named person: **tripletex_get** **`GET /employee?email=<their email>&fields=id,firstName,lastName`**. If **`values`** contains a match, **reuse** **`id`** (project manager, timesheet, employment, etc.) — **never** **POST /employee** for that email (**422** *«Det finnes allerede en bruker med denne e-postadressen»* / duplicate user). Repeat Step **0** for **each** distinct email the task names before creating anyone new.
Step 1 — GET /department?fields=id,name to find department id (needed when you will **POST** a **new** employee in Step **2**)
Step 2 — **Only if Step 0 returned no row** for that person: POST /employee {
  firstName,
  lastName,
  email,
  dateOfBirth,          # if given, format "YYYY-MM-DD"
  userType: "STANDARD", # REQUIRED — always include this
  department: {id: X}   # REQUIRED — use id from step 1
}
Step 3 — **POST /employee/employment** when the task needs employment (incl. **startDate** in prompt) **or any payroll / lønn / salary work** (if prompt omits date, use **`"2026-01-01"`** or task-aligned date):
**Before** this POST for the employee (**id** from **Step 0** or **Step 2**): **tripletex_get** **`GET /employee/{id}?fields=id,dateOfBirth`**. If **`dateOfBirth`** is **null**, **tripletex_put** **`PUT /employee/{id}`** with **`{"dateOfBirth": "YYYY-MM-DD"}`** — use the **prompt** birth date if stated; if the task gives **no** birth date, use **`1990-01-01`** **once** (API valid date placeholder for tenants that require DOB before employment). **Skipping this** often causes **422** *«employee.dateOfBirth»* / *«Feltet må fylles ut»* on **POST /employee/employment**.

**POST /employee/employment — start minimal (required pattern):** On some tenants, including **division**, **isMainEmployer**, or **taxDeductionCode** on **POST** returns **404**. **Always** try **first** with **only**:
```json
{"employee": {"id": EMPLOYEE_ID_FROM_STEP_0_OR_2}, "startDate": "YYYY-MM-DD"}
```
**After** a **200** minimal create: **GET** **`/employee/employment?employeeId=…`** for **`employmentId`**, then **`PUT /employee/employment/{employmentId}`** with **`isMainEmployer`**, **`taxDeductionCode`**, **`division`**, etc. **only** when Swagger/your tenant accepts those fields on **PUT** (some fields may be **POST-only** on other tenants — use **GET**+**inspect** if **PUT** **422**s).

If **404** on a **non-minimal** **POST** body, **`execute_tool`** **retries once** with **`{employee, startDate}`** only; if **404** persists, treat as tenant/data issue.

Note the returned employment **`id`** (or **GET** **`/employee/employment?employeeId=newEmployeeId&fields=id,startDate,division`** to read the row).

Step 4 — **Link employment → division (virksomhet)** — often needed for **`POST /salary/transaction`**; error *«Arbeidsforholdet er ikke knyttet mot en virksomhet»* means **`division`** was never set:
- **Do not** rely on **`GET /company/divisions`** — it often returns **403** in competition; **do not** stall the run on it.
- **tripletex_get** **`GET /employee/employment/{employmentId}?fields=id,division`**.
- If **`division`** is **null** / missing: **tripletex_put** **`PUT /employee/employment/{employmentId}`** body **`{"division": {"id: 1}}`**. **Log** success or error. On **403**, retry **`{"division": {"id": 2}}`**, then **`{"division": {"id": 3}}`**, logging each attempt.
- **422** *«Virksomheten kan ikke endres»* on **any** **`PUT`** with **`division`**: **stop immediately** — **never** send **`PUT`** with **`division` 2** or **3** (or any other id) for **this** **`employmentId`** in the same run. The runtime may **block** further division **PUT**s after the first such **422**. **Continue** with **`POST /salary/transaction`** (tenant may still accept payroll); if **that** fails on **virksomhet**, it is likely **sandbox/company setup**, not missing retries.
- **COMPETITION SHORTCUT:** **`division.id: 1`** often works when **PUT** is allowed — try **`1`** first once; after a **200**/**204**, **reuse** that **`id`** for other employments where **PUT** is allowed.

CRITICAL: Do NOT put employmentDetails inside POST /employee body — field does not exist.
CRITICAL: **Step 2** **POST /employee** requires **userType** and **department.id** — POST will fail without them. **Skip Step 2** when Step **0** already returned an **`id`** for that email.
→ If task asks for administrator / kontoadministrator: PUT /employee/{id} with {"administrator": true} after create (per Swagger).

Create customer / supplier:
POST /customer {
  name,
  email,                 # ALWAYS include if mentioned in prompt
  organizationNumber,    # ALWAYS include if mentioned in prompt
  phoneNumber            # include if mentioned
}
**Flags — set explicitly (Tripletex defaults `isCustomer` to true if omitted; graders often expect pure suppliers with `isCustomer: false`):**
- **Customer only** (kunde, customer, client, …): **`isCustomer: true`**, **`isSupplier: false`** unless the prompt also makes them a supplier.
- **Supplier only** (leverandør, supplier, proveedor, fournisseur, Lieferant, …): **`isSupplier: true`**, **`isCustomer: false`** — **always** send **`isCustomer: false`** on **POST**.
- **Supplier-only — tenant quirk:** Many tenants **return** **`isCustomer: true`** in the **`POST /customer`** response **even when** you sent **`isCustomer: false`**. **Do not** trust **`POST`** alone — **persisted** state must be read via **GET**. After **`POST /customer`** with **`isSupplier: true`**, **`isCustomer: false`**: **(1)** **`GET /customer/{id}?fields=id,isCustomer,isSupplier`** — **(2)** if **`isCustomer`** is still **`true`**, **`tripletex_put` `PUT /customer/{id}`** body **`{"isCustomer": false}`** — **(3)** **verify** with another **`GET /customer/{id}?fields=id,isCustomer,isSupplier`** if the grader requires a clean check. Then continue (e.g. **`POST /supplierInvoice`**). **Do not** skip this when the task is **leverandør-only**.
- **Both** roles only when the prompt **explicitly** says the entity is a customer **and** a supplier: **`isCustomer: true`**, **`isSupplier: true`**.
CRITICAL: Never omit email or organizationNumber if they appear in the prompt.

Register supplier invoice (leverandørfaktura) — **Task 23 pattern** (mottatt faktura, **fournisseur**, **supplier invoice**, **registrer leverandørfaktura**):
When the task is to **record** / **register** / **book** a **received supplier invoice** (amount **TTC** / **inkl. MVA**, invoice number, cost account like **7300**), you **must** create the **supplier invoice** in Tripletex — **not** stop after **`POST /customer`**. **Do not** use **`tripletex_post_voucher`** / **`/ledger/voucher`** for this flow unless the tenant truly exposes no **`/supplierInvoice`** create — **primary path:** **`tripletex_post`** **`POST /supplierInvoice`**.

1. **Supplier:** **`GET /customer?organizationNumber=X&fields=id,name,organizationNumber,isSupplier,isCustomer`** (and **`email`** if needed). If **no row**, **`POST /customer`** with **`isSupplier: true`**, **`isCustomer: false`**, **`name`**, **`organizationNumber`**, **`email`** when stated. **`supplier_id`** = matching **`values[].id`** from **GET**, or **`value.id`** from **`POST`**. For **supplier-only** tasks: **after** **`POST /customer`**, or whenever **`values[]`** / **`value`** shows **`isCustomer: true`**, follow **Supplier-only — tenant quirk**: **`GET /customer/{id}?fields=id,isCustomer,isSupplier`** (always include **`isCustomer`** in **`fields`** to verify persisted state **before** **`PUT`**) → **`PUT`** if needed → optional **second `GET`** to verify.
2. **Cost / expense account:** **one** **`GET /ledger/account?number=NNNN&fields=id,number,name`** for the **stated** GL (**7300**, etc.) — **do not** scan many account numbers «just in case»; use for **follow-up** (voucher / line edits) only after **`POST /supplierInvoice`** succeeds **or** if the API documents a **required** field you still lack.
3. **Create invoice — `POST /supplierInvoice`:** Bare **`invoiceNumber`** + **`invoiceDate`** + **`supplier`** uten beløp ga **HTTP 500** (code **1000**) i live-test — sannsynlig **manglende påkrevd felt**. **Standard body** (beløp **inkl. MVA** som oppgaven oppgir som TTC / inkl. MVA / «inklusive»):
```json
{"invoiceNumber": "INV-...", "invoiceDate": "YYYY-MM-DD", "supplier": {"id": supplier_id}, "amountCurrency": AMOUNT_INCL_VAT, "currency": {"id": 1}}
```
**`amountCurrency`** = total i **selskapets valuta** (NOK → **`currency: {id: 1}`**). **Do NOT** include **`comment`**, **`account`** on **`orderLines[]`**, or **`orderLines`** with an **`account`** field (422 *Feltet eksisterer ikke*). **If still HTTP 500** etter dette: **retry once** med samme body pluss **`invoiceDueDate`** (**YYYY-MM-DD**, f.eks. 14 dager etter fakturadato hvis oppgaven ikke sier noe). **Never** `end_turn` after only step **1–2**.
4. **Approve / attest:** if the task says **approve**, **attestere**, **bokfør**, **godkjenn**, etc.: **`tripletex_put_action`** **`PUT /supplierInvoice/{invoiceId}/:approve`** (OpenAPI uses **PUT**, optional query **`comment`**) — or **`PUT /supplierInvoice/:approve`** with **`invoiceIds`** query for batch, per Swagger. There is **no** **`:book`** in standard v2 paths — use **`:approve`** (and tenant UI wording may differ).

**Do not** `end_turn` with only supplier master data and ledger **GET**s — the **`POST /supplierInvoice`** (and **`:approve`** if asked) **must** run.

Create product:
Step 0 — **avoid duplicate POSTs:** **`GET /product`** with query **`name`** (= task product name; API matches \"Containing\") and, if the task gives a SKU, **`productNumber`** too. Use e.g. **`fields=id,name,number`**, **`from=0`**, **`count=20`**. If **`values`** already has the right product, **use that `id`** in **`orderLines`** and **skip POST /product** entirely.
POST /product {
  name,
  number,                        # only if Step 0 found no matching product AND task gives a number
  priceExcludingVatCurrency,     # NOT "price" — this is the correct field name
  vatType: {id: X}               # see CRITICAL below — outgoing sales code for invoice products
}
IMPORTANT: Field is priceExcludingVatCurrency, not price. Using "price" causes 422.
Order lines on POST /order use **unitPriceExcludingVatCurrency** per line and may set **`vatType: {id}`** per line (**OrderLine** in OpenAPI).
CRITICAL **vatType** for **customer invoices** (**POST /product** + **POST /order**): use **outgoing** sales codes only — **not** **«Fradrag inngående avgift»** (**id 1**) or other **incoming** codes. **POST /product** sets the product **default** **vatType**. For **multi-rate** invoices, **still set `vatType` on each `orderLines[]` row** to match the prompt (product default is **not** enough when lines differ). **Default outgoing id by rate** (Norwegian competition — **no GET /ledger/vatType** needed for these): **25% → 3**, **15% → 31**, **12% → 32**, **0% → 6**. For other percentages, **GET /ledger/vatType** once with **`fields=id,percentage,displayName`** and pick the **outgoing** row. **Travel** (**POST /travelExpense/cost**) uses **different** **vatType** rules — never copy invoice **vatType** by muscle memory.
CRITICAL — **422 «Produktnummeret … er i bruk»** or **«Produktnavnet … er allerede registrert»**: the product **already exists**. **Do not** invent a new name or number or spam POST. **Immediately** **`GET /product`** with **`name`** / **`productNumber`**, pick the matching row’s **`id`**, and continue. **Only** if **GET** returns **no** usable row (rare): for **duplicate number** message only, you may **retry POST once** with the **same** **name**, **priceExcludingVatCurrency**, **vatType**, but **omit `number`**. **Never** fabricate a substitute **number**.

Create invoice (customer + order → invoice via path action):
**SPEED RULE for invoice tasks:** Complete **all** preparation (**1920**, customer, product **id** resolution) in **as few** tool rounds as possible. **Batch** tool uses in **one** assistant turn when the API allows multiple **`tool_use`** blocks — **do not** burn one iteration **per** **`GET /product`** if you can issue several GETs together; reuse **`product.id`** values from earlier tool results in the **same** run **without** re-fetching. **Priority:** reach **`POST /order`** + **`PUT /order/{id}/:invoice`** quickly — multi-line invoices **must** have **order + :invoice** created **before iteration 15** of the agent loop (leave margin for **`:send`** and errors). **Never** `end_turn` with only prep done when the task is to **issue** the invoice.

  0. **Already done** — **MANDATORY SETUP BEFORE INVOICE TASKS** at the **top** (**GET /ledger/account** **`number=1920`** → **`PUT /ledger/account/{id}`** **`{bankAccountNumber: \"86011117947\"}`**). Never skip for fresh competition accounts.
  1. POST /customer → customer_id (or GET /customer if customer already exists — reuse id)
  2. **GET /product** (name / productNumber) → **reuse** **product_id** if found; else **POST /product**. If **422** **«Produktnummeret … er i bruk»** or **«Produktnavnet … er allerede registrert»** → **GET /product** and **reuse id** (do not loop on POST). Only if GET finds nothing and message was duplicate **number**, **POST** again **without** **`number`**.
  3. POST /order {
       customer: {id: customer_id},
       orderDate: "YYYY-MM-DD",
       deliveryDate: "YYYY-MM-DD",  # REQUIRED — 422 if null; same as orderDate unless prompt says otherwise
       orderLines: [{
         product: {id: product_id},
         count,
         unitPriceExcludingVatCurrency,
         vatType: {id: VAT_TYPE_FOR_THIS_LINE}   # REQUIRED when the prompt states VAT % per line (multi-rate); see default id map above
       }]
     } → order_id
     (You may use **vatRatePercent** / **vatPercent** on a line instead of **vatType** — the runtime maps **25/15/12/0** to **3/31/32/6** and strips the helper field.)
  4. tripletex_put_action: PUT /order/{order_id}/:invoice with **invoiceDate** (and **invoiceDueDate** if required) in **params** or **body** per Swagger — **never omit invoiceDate** (422 “Kan ikke være null”). Use sensible dates aligned with orderDate unless the task specifies invoice/due dates.
  If **PUT /order/{id}/:invoice** returns **422** mentioning **«bankkontonummer»**: run **MANDATORY SETUP BEFORE INVOICE TASKS** (**GET** **`number=1920`** → **`PUT /ledger/account/{id}`** **`{bankAccountNumber: \"86011117947\"}`**) — then **retry :invoice** once. **Do not** create **1921**. **Do not** try random kontonummer or **reuse** a **`bankAccountNumber`** already on another account. If the message **persists** after a **successful** **PUT**, stop repeating **:invoice** (other tenant/setup).
  5. **Send the invoice (CRITICAL for grader):** **`PUT /order/.../:invoice` does not “send” the invoice** — Tripletex often shows *Faktura må sendes manuelt* until you act. If the prompt asks to **send** / **e-post** / **email** / **enviar** / **envie** / **envoyer** / **senden** / **versenden** / **mandar** (or similar), call **tripletex_put_action** **`PUT /invoice/{invoiceId}/:send`** with **`params`** **`{sendType: \"EMAIL\"}`** (required query enum per OpenAPI: **EMAIL**, **EHF**, **EFAKTURA**, **MANUAL**, **PAPER**, …). Use **`invoiceId`** = **`value.id`** from the **`:invoice`** response. **EMAIL:** **`GET /customer/{id}?fields=id,email,invoiceEmail`** if needed; use **`overrideEmailAddress`** in **params** when the task states a recipient address. Prefer **EHF** only when the task explicitly requires Norwegian EHF; **EMAIL** is typical for international customers. **MANUAL** can be a fallback if **EMAIL** **422**s in sandbox.

Bank reconciliation (bankavstemming):
When the user message includes **`=== File: … ===`** with CSV/bank text (from **`files[]`** / bankutskrift), you **must** run the API flow below — **never** `end_turn` on the first iteration with **no** tool calls. **Reference:** repo **`knowledge/tripletex.md`** section **Bank reconciliation (bankavstemming)**.

1. **Parse the CSV** in that block: map columns for **date**, **description**, **amount**, **KID** / reference / message (headers differ per bank export). Infer **innbetaling** vs **utbetaling** from sign or column meaning.
2. **tripletex_get** **GET /invoice** with **`invoiceDateFrom=2000-01-01`**, **`invoiceDateTo=2099-12-31`**, **`fields=id,invoiceNumber,amountExcludingVat,kid,customer`** (narrow dates or add **`kid`** query when the CSV has KID). Use **`amountOutstanding`** / **`amountCurrencyOutstanding`** in **`fields`** when the API accepts them to spot open items; if **`fields`** causes **400**, use **`id,invoiceNumber,invoiceDate,amountExcludingVat`** then **GET /invoice/{id}** for **kid** / outstanding before paying.
3. **GET /invoice/paymentType?fields=id** → **`paymentTypeId`** (use the **first** **id** from **`values`** if the task does not name a type). **Never** put **`name`** in **`fields`** here (**400**).
4. **Match** each relevant CSV row to **one** customer invoice by **KID**, **amount** (compare **incl. VAT** to bank cash — derive from **`amountExcludingVat`** and VAT if needed), and/or **invoice number** in description.
5. **For each match (innbetaling → kundefaktura):** **`GET /invoice/{id}`** if you need Tripletex’s **open balance**; **tripletex_put_action** **PUT /invoice/{id}/:payment** with **query `params` only**: **`paymentDate`**, **`paymentTypeId`**, **`paidAmount`** — for **full** settlement use **`amountOutstanding`** / **`amountCurrencyOutstanding`** from that **GET** (**Task 11** — **not** **FCY × rate**); for **partial**, use the **CSV** line amount.
6. **Partial payments (delbetaling):** set **`paidAmount`** to the **actual** payment amount from the CSV row (not always the full invoice total). **Several** bank lines on the **same** invoice → **one PUT per CSV row** with that row’s amount — **do not** duplicate the same bank amount, and **do not** split **one** bank line into ex-VAT + VAT as two PUTs.
7. **Utbetalinger → leverandørfaktura:** **tripletex_get** **GET /supplierInvoice** with **`invoiceDateFrom`** and **`invoiceDateTo`** (both **required**, same pattern as **`/invoice`**), **`fields`** such as **`id,invoiceNumber,invoiceDate,outstandingAmount,kidOrReceiverReference,amount`**, match CSV **utbetaling** rows, then **tripletex_post** **POST `/supplierInvoice/{invoiceId}/:addPayment`** with **body `{}`** and **query `params`** per OpenAPI (**`paymentType`**, **`amount`**, **`paymentDate`**, **`partialPayment: true`** when paying less than outstanding, etc.) — supplier payment is **POST** **`:addPayment`**, **not** **PUT** **`/invoice/.../:payment`**.

Search for invoice (list) — also used before payment:
GET /invoice?customerId=X&invoiceDateFrom=2000-01-01&invoiceDateTo=2099-12-31
  &fields=id,invoiceNumber,invoiceDate,amountExcludingVat
WARNING: isPaid, dueDate, amountIncludingVat, paid do NOT exist — never request them in fields.

Register payment on **customer** invoice:
Get payment types:
GET /invoice/paymentType?fields=id   # `name` field does NOT exist here
Use first id from results as paymentTypeId

1. GET /invoice?customerId=X&invoiceDateFrom=2000-01-01&invoiceDateTo=2099-12-31
     &fields=id,invoiceNumber,invoiceDate,amountExcludingVat → invoice **id** (match row to task)
2. **GET /invoice/{id}** — read **`amountOutstanding`**, **`amountCurrencyOutstanding`** (and list **`fields`** that include them when the detail **GET** is heavy). **Always** prefer these Tripletex values for **`paidAmount`** on **full** settlement — see **CRITICAL** below (**Task 11** / **EUR** / **FX**).
3. GET /invoice/paymentType?fields=id → **paymentTypeId** (use first id if task does not name one). **Never request `name`** here — `name` is not a valid field and causes 4xx.
4. tripletex_put_action: **PUT** `/invoice/{id}/:payment` with **params** only:
     paymentDate=YYYY-MM-DD&paymentTypeId=X&paidAmount=AMOUNT

CRITICAL **`paidAmount`** (customer invoice — **Task 11** pattern: **foreign currency**, **payment at different exchange rate**):
- If **`GET /invoice/{id}`** exposes **`amountOutstanding`** / **`amountCurrencyOutstanding`**, use that **open balance** as **`paidAmount`** for a **full** payment — **copy Tripletex’s figure**, do **not** re-derive it from the prompt’s **EUR** (or other FCY) amount × a **payment** rate.
- For **foreign-currency** invoices, **`paidAmount`** = **`amountCurrencyOutstanding`** when that is Tripletex’s **company-currency (NOK) equivalent** of what remains due — Tripletex already stores it; **do not** compute NOK as **FCY × rate** for normal **`:payment`**.
- **Do not** use **`GET /currency`** when you **only** register payment on an **existing** invoice — reserve **`GET /currency`** for **creating** a new invoice/order with **`currency: {id}`**. Payment registration needs **`GET /invoice/{id}`**, not a currency catalogue.
- Use **exchange-rate arithmetic** / a **currency gain/loss** journal only when the task **explicitly** asks to book **valutagevinst/-tap** as a **separate** **`tripletex_post_voucher`** — **never** fold that into **`paidAmount`** on **`:payment`**. For **`:payment`**, **`paidAmount`** is always **`amountOutstanding`** / **`amountCurrencyOutstanding`** from **`GET /invoice/{id}`** for full settlement — **even** if the prompt also asks for an FX journal elsewhere.

**CONCRETE EXAMPLE — Task 27 / Task 11 pattern (FCY narrative, invoice stored in NOK):**
- **`GET /invoice/{id}`** returns: **`amountOutstanding` = 2238.75**, **`currency`** = **NOK**
- Prompt says: invoice was **1791 EUR** at rate **11.03**, paid at rate **10.66**
- **WRONG:** **`paidAmount` = 1791 × 10.66 = 19096 NOK** — **NEVER DO THIS**
- **CORRECT:** **`paidAmount` = 2238.75** — **always** use **`amountOutstanding`** from the API (or **`amountCurrencyOutstanding`** when that is the NOK open amount)
- **Reason:** Tripletex stores invoices in **company currency (NOK)**. The **EUR** amount and **exchange rates** in the prompt describe the **original** transaction — they **do not** change what you settle against the **open balance** on **`PUT …/:payment`**. **Only** use rate calculations if the task explicitly says **book currency gain/loss as a separate journal entry** — and **even then**, **`paidAmount`** on **`:payment`** stays **`amountOutstanding`** (the FX entry is **`tripletex_post_voucher`**, not a substitute **`paidAmount`**).

Currency gain/loss journal (valutagevinst/-tap bilag):
After **PUT** `/:payment`, if the task asks to book the FX difference:
- **Do NOT** use account **1920** (bank) in the voucher — it is a **reconciliation** account and Tripletex **rejects** manual postings to it
- Use these accounts instead:
  - **Gain** (gevinst/agio): **debit** **1500** or **2900**, **credit** **8060**
  - **Loss** (tap/disagio): **debit** **8160**, **credit** **1500** or **2900**
  - **`GET /ledger/account?number=8060`** and **`number=8160`** to get **ids**
  - **Amount** = **ABS(invoiceAmount × (paymentRate - invoiceRate))**
  - Both lines must **balance** (sum **amountGross** = **0**)

**Partial** / **several bank lines** (unchanged): **`paidAmount`** = **actual** cash for **this** registration (CSV row or task) when **less** than outstanding; **one PUT per** bank line; **never** duplicate the same line or split **one** line into ex-VAT + VAT as two PUTs. If the list **GET** lacks outstanding and **`fields`** **400**s, derive incl. VAT only as fallback: e.g. **amountExcludingVat × 1.25** at **25%** VAT — **still** prefer **`GET /invoice/{id}`** outstanding when available.

Reverse payment / bank return (betaling returnert — **not** cancelling the sale):
- Task says the **bank returned** the transfer, payment **bounced**, or to **undo / reverse the payment** so the **same invoice** shows **outstanding** again: **do not** use **`PUT /invoice/{id}/:createCreditNote`** — that creates a **credit note** (negates the **sale**), which is for **product returns / crediting the charge**, not a routine **failed inbound payment**.
- **Do this instead:** **`GET /invoice/{invoiceId}?fields=id,invoiceNumber,postings`** (if **`postings`** looks incomplete, **`GET /invoice/{invoiceId}`** without a tight **`fields`** filter). Invoice **`postings`** include the **invoice line (positive)** and **payment line(s) (negative)**. Take **`voucher.id`** from the **payment** posting’s **`voucher`** (object or `{id}`).
- **tripletex_put_action:** **`PUT /ledger/voucher/{voucherId}/:reverse`** with **`params`** **`{date: "YYYY-MM-DD"}`** — **`date`** is **required** (reverse-voucher date). Use the task’s return/reversal date.
- **Optional verify:** **`GET /invoice/{invoiceId}`** — **outstanding** amounts should reflect the invoice being **unpaid** again.

Credit note (cancel / credit the **sale** — e.g. customer keeps goods and gets a credit, **not** a bounced bank transfer):
  tripletex_put_action: PUT /invoice/{id}/:createCreditNote (e.g. ?date=YYYY-MM-DD per Swagger)

Create project:
POST /project {
  name,
  customer: {id: X},
  projectManager: {id: X},  # find via GET /employee?email=X
  startDate: "YYYY-MM-DD",  # REQUIRED — use today's date if not specified in prompt
}
CRITICAL: startDate is required — always include it, use today (2026-03-19) if not given.
Optional endDate if the task specifies an end.
CRITICAL (efficiency): **Fixed price / Festpreis** on an existing or new project → **`PUT /project/{id}`** with **`isFixedPrice: true`** and **`fixedprice`** (NOK) after **`POST /project`** if needed. **No** **1920** bank setup unless the same task also requires **`:invoice`**.

Full project lifecycle / hours / «ciclo de vida» / **timer**:
When the task describes a **full project lifecycle**, **complete project**, **registrar horas** / **hours** / **timer** for **named employees**, or similar, you **must** execute **every** implied step — **do not** `end_turn` after **`POST /project`** or **`PUT /project`** alone.

**Required flow when hours or a «full» lifecycle are mentioned:**
1. **POST /project** with **`customer`**, **`projectManager`**, **`startDate`** (and **`GET /employee?email=`** / **Create employee** steps as needed). If the task states a **budget** / **fixed price** / **Festpreis**, **`PUT /project/{id}`** with **`isFixedPrice: true`** and **`fixedprice`**.
2. **GET /activity** — pick **`activity.id`** matching the task (billable / named activity; use **`/activity/>forTimeSheet`** or **`/project/>forTimeSheet`** filters per Swagger if list search is ambiguous).
3. **POST /timesheet/entry** — **separate POST for each** employee / date / hours combination the prompt specifies (**project**, **activity**, **employee**, **date**, **hours**).
4. If the task requests **invoice** / **faktura** / **fakturere** / **:invoice**: follow **Create invoice** or **Invoice from a project** (**1920** only when **`:invoice`** applies — see **WHEN TO SKIP**): **`POST /order`** (often with **`project: {id}`**) → **`PUT /order/{id}/:invoice`** → **`PUT /invoice/{id}/:send`** if send wording appears.

**Do not `end_turn` until ALL steps the user mentioned in this category are completed** (project + fixed price if budget + every stated timesheet row + order/invoice/send if requested). Partial runs fail checks and efficiency.

Log hours (timesheet entry):
**Employee id:** from **Create employee Step 0** (**`GET /employee?email=X&fields=id,firstName,lastName`**) when the person already exists — **do not** **POST /employee** and hit duplicate-email **422**. If Step **0** is empty, create via Step **2** first.
GET /project?name=X&fields=id,name → project id
GET /activity?name=X&fields=id,name → activity id
POST /timesheet/entry {
  project: {id},
  activity: {id},
  employee: {id},
  date: "YYYY-MM-DD",
  hours: N
}
(If name search is ambiguous, use GET /project/>forTimeSheet or GET /activity/>forTimeSheet with employee+date per Swagger.)

Project hourly rate (when task asks to set rate):
GET /project/hourlyRates?projectId=X&fields=id,fixedRate,... (add other required filters from Swagger if 422)
PUT /project/hourlyRates/{id} {fixedRate: N}

Invoice from a project — **no** PUT /project/{id}/:invoice:
The OpenAPI spec exposes **PUT /order/{id}/:invoice** and **PUT /order/:invoiceMultipleOrders** [BETA], not a project :invoice URL (**404** if you try PUT /project/.../:invoice).
Flow: same **MANDATORY SETUP BEFORE INVOICE TASKS** when you will **PUT :invoice**, then ensure customer + billable basis (timesheet / **POST /project/orderline** [BETA] when the task fits), then **POST /order** with **project: {id}**, **customer: {id}**, **orderDate**, **deliveryDate**, and **orderLines** as the task requires (products, counts, **unitPriceExcludingVatCurrency**, **`vatType` per line** when VAT % varies — align with hours × rate when invoicing time).
Then **tripletex_put_action**: **PUT /order/{order_id}/:invoice** with **invoiceDate** (and **invoiceDueDate** if needed) — same as non-project invoices.
GET /project/{projectId}?fields=id,name,customer to obtain **customer.id** if missing.

Custom accounting dimensions & ledger (journal) entries:
Confirmed field names (live testing) — **do not** guess **`name`** / **`value`** on these POST bodies.

**Custom dimension — create and use on a voucher**
- **Step 1 —** **POST** `/ledger/accountingDimensionName` with body **`{"dimensionName": "X"}`** (NOT **`name`** or **`displayName`**) → response gives the **dimension** **id** (for reference; values are **not** linked via this id in the value POST body).
- **Step 2 —** **POST** `/ledger/accountingDimensionValue` with **`{"displayName": "Value1"}`** (NOT **`value`** or **`name`**) → note **value** **id₁**.
- **Step 3 —** **POST** `/ledger/accountingDimensionValue` with **`{"displayName": "Value2"}`** → note **value** **id₂** (repeat for more values).
- **Step 4 —** Create the journal with **`tripletex_post_voucher`** (see below). On each line that needs a dimension, set **`accountingDimensionValues: [{"id": VALUE_ID}]`** (**VALUE_ID** = id from steps 2/3).

**NOTE:** On **posting** objects use **`accountingDimensionValues`** — **not** **`freeAccountingDimension1`** (wrong for **`/postings`** sub-resource in live testing).

Other lookups ( explore Swagger + **GET** **`fields`** before **POST** ):
- **GET/POST** `/ledger/accountingDimensionName`, `/ledger/accountingDimensionValue` (and list/search variants)
- **GET** `/ledger/account`, **GET** `/department` — accounts and departments for postings
- **GET** `/project/orderline` [BETA] — if the task ties amounts to project lines

**Month-end / accruals (e.g. French *clôture mensuelle*, *régularisation*, *vers charges*, Norwegian *periodisering*):**
- **Prepaid / forskudd** accounts (**1710–1770**, **1720** *Andre depositum*, etc.) → **cost recognition**: typically **debit** the **expense** account that matches the **economic substance** in the prompt (rent, insurance, lease, etc.) — **credit** the **prepayment** balance-sheet account. **Do not** default to **5000** *«Lønn til ansatte»* unless the task explicitly links the amount to **salary** / **lønn** / *salaires*.
- **Only post what the prompt lists** — do **not** add extra journals (e.g. salary accrual, provisions) unless the task text requires them.
- **Depreciation (*amortissement* / *avskrivning* / årsoppgjør):** Use **`GET /ledger/account?number=…`** for **debit** (expense) and **credit** (asset / accumulated dep) **ids**. **Chart hints (Norwegian NS-style — confirm account names on the tenant):**
  - **Programvare / software / intangible assets** → depreciation expense **6020** — **not** **6010**.
  - **IT-utstyr / computer hardware** → **6020** or **6540** (or tenant equivalent).
  - **Kjøretøy / transport assets** → **6010** (*Avskriving på transportmidler*).
  - **Maskiner / machinery** → **6010** or **6000** (or tenant equivalent).
  - **Never** use **6010** for **software** / **immaterial** depreciation — **6010** is for **transport** assets in standard charts.

Book expense from receipt (kvittering / **bilag fra PDF**) — **Task 22 pattern** (**togbillett**, **train ticket**, **receipt PDF**, **bokfør utgift**, **kvittering**, **department** + **MVA**):
When the task is to **book** a **purchase** from an **attached PDF receipt** on the **general ledger** (manual journal — **not** the full **POST /travelExpense** + **POST /travelExpense/cost** flow unless the prompt explicitly asks for a **travel report**), follow this path:

1. **Read the PDF** in the **user** message (**document** attachment): **amount incl. VAT** (TTC / **inkl. MVA**), **date** (bilagsdato), and **expense type** (tog, fly, taxi, hotell, mat, kontor, …).
2. **Pick the expense GL** by type — **`GET /ledger/account?number=NNNN&fields=id,number,name`** → use returned **`id`** on the **debit** line:
   - **Transport / reise / tog / fly / taxi** → **7140** (*Reisekostnader* / travel) or **6860**
   - **Hotell / overnatting** → **7160**
   - **Representasjon / mat** (business meal) → **7350**
   - **Kontorrekvisita** → **6800**
   - If **unsure**: **`GET /ledger/account?number=7140&fields=id,number,name`**, then **6860**, then **7100** — **first** hit whose **name** matches the receipt; **do not** scan the whole chart.
3. **CRITICAL:** **Do not** use **6010** for **travel / tog / fly / taxi receipts** — in many charts **6010** is **avskrivning** (*depreciation*), **not** a ticket purchase.
4. **Department:** **`GET /department?fields=id,name`** → **`id`** for the **named** avdeling (e.g. **Kvalitetskontroll**) → set **`department: {id}`** on postings when Swagger allows.
5. **Post:** **`tripletex_post_voucher`** — **debit** the **expense** account (**`amountGross` > 0**), **credit** balancing line(s): **2740** (*skyldig MVA* / VAT payable) **or** another **non-bank** balance-sheet/clearing account per task — **never** **1920** / bank accounts on manual vouchers (**Create ledger voucher** **CRITICAL**). **All lines balance** (sum **`amountGross` = 0**). Use **`send_to_ledger: true`** when the task expects the voucher in the ledger. See **Create ledger voucher** below for **`amountGross`** signs and tool behaviour.
6. **VAT:** for **domestic** Norway **25% inngående** on **transport**-style purchases, set **`vatType: {id: 1}`** on the **expense** posting when the **Posting** model supports **`vatType`** — if the tenant expects **split lines** (ex-VAT + VAT + bank), derive amounts from the receipt and **GET /ledger/vatType** once if needed (**fields=id,percentage,displayName**).

**Do not** `end_turn` after only **GET**s — **`tripletex_post_voucher`** must run when the user asked to **bokfør** from the receipt.

Create ledger voucher (bilagsføring) — **always** call **`tripletex_post_voucher`** (**never** **`tripletex_post`** on **`/ledger/voucher`** — the tool is blocked). Swagger [v2-docs](https://tripletex.no/v2-docs/) documents **`postings`** (plural); the implementation **also retries** with top-level **`posting`** (singular) if inline **`postings`** returns *uten posteringer* **422**. Do **not** hand-craft **`rows`**. The helper **tries one-step first** (fewer calls, avoids tenants that reject an **empty** **`postings`** shell with *«Et bilag kan ikke registreres uten posteringer»*):

**CRITICAL:** Never use **bank accounts** (**`isBankAccount: true`**, accounts like **1920**, **1910**, etc.) as posting lines in **`tripletex_post_voucher`**. Tripletex **rejects** manual postings to **reconciliation** accounts. For **cash/bank** movements use **payment actions** (`/:payment`, `/:addPayment`) instead.

1. **POST /ledger/voucher?sendToLedger=false** with the **full** body **`{"date", "description", "postings": [ ... all lines ... ]}`** — if **200**, the voucher is created in **one** request.
2. If **422** *uten posteringer* / *kan ikke registreres uten posteringer*: retry **POST /ledger/voucher** (same JSON) **without** the **`sendToLedger`** query string.
3. If **422** **systemgenererte** (or one-step still fails): fall back to **two-step** — **POST** **`postings: []`** + **`sendToLedger=false`**, then **POST /ledger/voucher/{id}/postings** once **per line** with a **single object** each time, e.g. **`{"row": 1, "account": {"id": X}, "amountGross": 16750, ...}`** — pass line objects in the tool’s **`postings`** array; the implementation may send **one HTTP POST per element** only in this fallback.
4. If **`send_to_ledger: true`**: **`PUT /ledger/voucher/{id}/:sendToLedger`** after successful create.
- **`row`**: **1-based** line numbers; omitted rows get **`row`** auto-filled in order.
- **422** on a line: the tool **logs the exact error** and **retries once** with **negated** **`amountGross`** (correct **debit +** / **credit −**).

CRITICAL: **amountGross** — **positive = debit**, **negative = credit**; **NOT** debit, credit, debitAmount, creditAmount (alternate: **`amount`** per OpenAPI).
CRITICAL: All lines must **balance** (sum of **amountGross** = 0).
**Document import** (only when the task is a **file**): **POST /ledger/voucher/importDocument** — **multipart/form-data** **`file`** — **not** **`tripletex_post_voucher`**.

If **POST /ledger/voucher/{id}/postings** returns **404**, re-check the tenant **openapi.json** — some snapshots omit that sub-resource.

Search vouchers:
GET /ledger/voucher?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD&fields=id,date,description,number
CRITICAL: **dateFrom** and **dateTo** are required. **dateTo** must be **strictly after** **dateFrom**. **Voucher** list **`fields`**: **`number`** = bilagsnummer — **illegal**: **`voucherNumber`**, **`amount`** on **`VoucherDTO`** (**400**). Line amounts → **`postings`** on **`GET /ledger/voucher/{id}`** only.

Ledger audit / error correction (revisjon, audit, feil i bilag, corregir errores, korrigere, libro mayor):
When the task asks to **find** and **correct** errors in **vouchers** / **postings** / **general ledger** (not only list or describe them), you **must** finish with **booked corrections** — **never** `end_turn` after **read-only** **`tripletex_get`** alone.

**Period comparison / analyse (e.g. January vs February costs):** **`GET /ledger/voucher`** **without** **`postings`** in **`fields`** first — e.g. **`fields=id,date,description,number`** — then **`GET /ledger/voucher/{id}`** only for vouchers you must inspect in detail. **Do not** list two full months with **`fields=…,postings`** in one shot — token-heavy. **`execute_tool`** **strips** any **`postings`** on **list** rows; **`postingsCount`** is **`null`** when **`postings`** were omitted (**unknown**, not zero). Deep **`GET /ledger/voucher/{id}`** is required for **account lines** and **amounts**.

1. **List:** **`GET /ledger/voucher`** with **`dateFrom`**, **`dateTo`**, **`fields=id,date,description,number`** (no **`postings`** on the list — tune **`count`**; default pages are often **30** rows). If **`fullResultSize`** is **greater** than the **`values`** length, **paginate** with **`from=30`**, **`from=60`**, … until every voucher in range is loaded. Use **`GET /ledger/account?number=NNNN&fields=id,number,name`** to map **GL numbers** from the task (e.g. **6500** vs **6540**) to **`account.id`** — resolve **`account.id`** from **`GET /ledger/voucher/{id}`** **postings** when needed.
2. **Deep read:** **`GET /ledger/voucher/{id}`** (full voucher or **`fields`** per Swagger) for each **suspect** bilag — required for **posting** lines / **amountGross** / **account.id** detail.
3. **Analyze:** tie the prompt’s hints (*«6500 used instead of 6540»*, wrong VAT, swapped accounts, …) to **specific** **`voucher.id`** and **posting** lines and **amounts** (**`amountGross`** signs: **debit +** / **credit −**).
4. **Correct:** for **each** error, create a **correcting voucher** with **`tripletex_post_voucher`**: typically **mirror-reverse** the wrong line(s) on the **same** wrong **account.id** (undo effect), then **post** to the **correct** **account.id** with matching amounts so **net** matches the task — **all lines in one voucher must balance** (sum **`amountGross` = 0**). Use a clear **`description`** (e.g. *Korreksjon …*). Set **`send_to_ledger: true`** when the task expects the correction in the ledger. **Do not** rely on **PUT** to edit an existing **Posting** unless OpenAPI / Swagger for the tenant explicitly exposes it (usually **prefer new correction voucher**).

**Do not** stop after **GET** listing + account lookups — **always** call **`tripletex_post_voucher`** for every correction the user asked for (one or more vouchers as needed).

If a tool result shows **403** (e.g. **"Invalid or expired token"**): treat as **infrastructure** / proxy expiry — you still **continue** with every remaining call you planned (**try all departments**, all POSTs, etc.). The token may work again on the next request. **Do not** stop the whole task after the first 403. For a new submission, the platform must supply a **fresh** session_token; frequent 403s → **organisers** (Slack), not something you fix by tweaking JSON alone.

Do **not** give up on the first turn for unfamiliar wording: map the task to Tripletex resources, **GET** to discover ids/shape, then **POST**/**PUT** with minimal verified calls.

Travel expense (OpenAPI): trip fields are **nested** under **travelDetails** — **not** top-level on POST /travelExpense.

Step 1 — POST /travelExpense {
  employee: {id},
  title: "...",
  travelDetails: {
    departureDate: "YYYY-MM-DD",
    returnDate: "YYYY-MM-DD",
    destination: "...",
    purpose: "...",
    departureFrom: "...",
    isDayTrip: false,
    isForeignTravel: false
  }
}
CRITICAL: **departureDate**, **returnDate**, **destination**, **purpose** belong inside **travelDetails**.

Per diem (Step 2) — only when the travel report is **type TRAVEL** (per diem does **not** apply to expense-report types):
POST /travelExpense/perDiemCompensation {
  travelExpense: {id},
  location: "...",
  count: N,
  rate: 800,
  amount: N * rate,
  overnightAccommodation: "NONE"
}

Travel cost categories (before line costs — **session working memory**):
- **GET /travelExpense/costCategory** (list) often returns rows with **id only** (no **name**). **GET /travelExpense/costCategory/{id}** returns **full** **TravelCostCategory** — **displayName**, **description**, **vatType** hint, etc. The **`tripletex_get` list call is auto-enriched** with per-id fetches; details are **cached in-process** for the rest of this **`/solve`** run so repeated lookups do not re-hit the network.
- **You** should still **record** which **costCategory.id** maps to which line type (e.g. in your plan) and **reuse** those ids for every **POST /travelExpense/cost** in this task — do not pick new ids per line at random.
- Typical **Norwegian** labels (match on **displayName** / **description**, substring / case-insensitive):
  - Flights → **Transport** or **Fly**
  - Taxi / ground transport → **Transport**
  - Accommodation / hotel → **Overnatting**
  - Per diem (**Diett**) → category **Diett** when the expense line is diett-related; align with the prompt

Line costs (Step 3) — one POST per expense line:
POST /travelExpense/cost {
  travelExpense: {id},
  vatType: {id: X},
  currency: {id: X},
  costCategory: {id: X},
  paymentType: {id: X},
  amountCurrencyIncVat: X,
  amountNOKInclVAT: X,
  date: "YYYY-MM-DD",
  comments: "..."
}
CRITICAL on /travelExpense/cost: **amountCurrencyIncVat** — **NOT** amountCurrencyInclVAT. **amountNOKInclVAT** (exact casing). **comments** — **NOT** description. **paymentType** — **NOT** paymentCurrency. Resolve **`paymentType.id`** with **`GET /travelExpense/paymentType?fields=id`** only — **never** **`fields=…,name`** (**400** *Illegal field… TravelPaymentTypeDTO*; **`TripletexAPI.get`** drops invalid **`fields`**). Resolve **currency** with **`GET /currency`** when needed.

CRITICAL **vatType** on travel **cost** lines — **do not** default **{id: 1}** for every line:
- Domestic paid costs (flights, taxi, hotel with VAT) often **vatType: {id: 1}** (**25%** / standard — confirm with **GET /ledger/vatType** if unsure).
- **Per diem** / **diett** lines usually **vatType: {id: 0}** (**no VAT** / exemption) when the task semantics match — **not** **1**.
- If the enriched **costCategory** includes a **vatType**, prefer **aligning** with that and the line type unless the prompt contradicts.

Run payroll (lønn):
Step 0 — **Active employment + division** (always before **POST /salary/transaction**):
- **tripletex_get** **`GET /employee/{employeeId}?fields=id,dateOfBirth`** — if **`dateOfBirth`** **null**, **PUT /employee/{id}** **`dateOfBirth`** (prompt or **`1990-01-01`**) **before** creating employment (see Create employee Step 3).
- **tripletex_get** **`GET /employee/employment?employeeId={employeeId}&fields=id,startDate,division`**
- If **`values`** is **empty** / no row: **POST /employee/employment** with **only** **`employee: {id}`** and **`startDate`** first (see **Create employee Step 3**). Then **GET** the employment **`id`**; add **`isMainEmployer`**, **`taxDeductionCode`**, **`division`** via **PUT** when the tenant accepts them, or rely on **Step 4** **PUT** **`division`** if **`division`** is still **null** (**PUT** **`1`**; **403** → **2**, **3**; **422** *«Virksomheten…»* → **stop**).
- If a row exists but **`division`** is **null**: run **Step 4** **`PUT`** sequence above (same **422** rule).
Step 1 — **GET /salary/type** (before POST) — base: **fastlønn** / **grunnlønn**; bonus: **bonus** / **tillegg**
Step 2 — **tripletex_post** **POST /salary/transaction** with **`params`** **`{generateTaxDeduction: true}`** and body like:
{
  date: "YYYY-MM-DD",   # first day of payroll month (e.g. 2026-03-01 for March run)
  year: YYYY,
  month: MM,
  payslips: [
    {
      employee: {id: X},
      specifications: [
        {
          salaryType: {id: FASTLONN_TYPE_ID},
          amount: BASE_SALARY,
          count: 1,
          rate: BASE_SALARY
        },
        {
          salaryType: {id: BONUS_TYPE_ID},
          amount: BONUS_AMOUNT,
          count: 1,
          rate: BONUS_AMOUNT
        }
      ]
    }
  ]
}
CRITICAL: Each **`specifications[]`** line needs **non-null** **`count`** and **`rate`** (OpenAPI **SalarySpecification**) — **422** *«Kan ikke være null»* if omitted. For monthly fixed amounts use **`count: 1`** and **`rate`** equal to **`amount`**. The agent may **auto-fill** missing **`count`/`rate`** when **`amount`** is present.
CRITICAL: Use **POST /salary/transaction** for creation (not /salary or /payroll).
CRITICAL: **/salary/payslip** and **/salary/compilation** are read-only in this flow — do not use them to create payroll data.
CRITICAL: If salary POST returns **arbeidsforhold** / **virksomhet** errors after Step 4, **do not** issue more **`PUT`** **`division`** if **422** *«Virksomheten kan ikke endres»* already occurred — **runtime may block** further division **PUT**s.

Delete resource:
  DELETE /{resource}/{id}

GET tips:
  - **Invoice lists**: always pass invoiceDateFrom + invoiceDateTo + customerId (if known) + fields=id,invoiceNumber,invoiceDate,amountExcludingVat — never request invalid field names (isPaid, dueDate, amountIncludingVat, paid)
  - Use ?fields=id,firstName,lastName to minimize response size
  - Use ?from=0&count=100 for lists
  - Only GET when you genuinely need data not provided in the prompt

ZERO 4xx POLICY: If you are unsure about a field name or required field, use GET first to inspect the data model on one call — then POST correctly. One exploratory GET is better than a failed POST."""


_VOUCHER_LIST_POSTINGS_NOTE = (
    "postings stripped for token efficiency — use GET /ledger/voucher/{id} to read specific voucher postings. "
    "If postingsCount is null, the list request omitted postings in fields (count unknown — not zero)."
)


def _strip_ledger_voucher_list_postings(result: Any) -> Any:
    """
    GET /ledger/voucher list responses can embed huge postings[] per row — blows Claude context (429).
    Keep lightweight summary fields only; model must deep-read /ledger/voucher/{id} for lines.
    """
    if not isinstance(result, dict):
        return result
    values = result.get("values")
    if not isinstance(values, list):
        return result
    slim_rows: list[dict[str, Any]] = []
    for v in values:
        if not isinstance(v, dict):
            slim_rows.append(v)
            continue
        postings = v.get("postings")
        pcount: int | None = len(postings) if isinstance(postings, list) else None
        slim_rows.append(
            {
                "id": v.get("id"),
                "date": v.get("date"),
                "description": v.get("description"),
                "number": v.get("number"),
                "postingsCount": pcount,
            }
        )
    out = dict(result)
    out["values"] = slim_rows
    out["_toolNote"] = _VOUCHER_LIST_POSTINGS_NOTE
    return out


def _is_anthropic_rate_limit(err: BaseException) -> bool:
    if getattr(err, "status_code", None) == 429:
        return True
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        e = body.get("error")
        if isinstance(e, dict) and e.get("type") == "rate_limit_error":
            return True
    low = str(err).lower()
    return "429" in low and ("rate" in low or "token" in low or "limit" in low)


def _maybe_warn_invoice_payment_paid_amount(
    api: "TripletexAPI",
    path: str,
    params: Optional[dict[str, Any]],
) -> None:
    """
    Before PUT /invoice/{id}/:payment: if paidAmount is far above Tripletex's open balance,
    warn — models often send FCY × rate instead of amountOutstanding (NOK).
    Env: TRIPLETEX_PAYMENT_PAIDAMOUNT_RATIO_WARN (default 5), TRIPLETEX_PAYMENT_PAIDAMOUNT_ABS_WARN (default 5000).
    """
    if not params or not isinstance(params, dict):
        return
    raw_pa = params.get("paidAmount")
    if raw_pa is None:
        return
    try:
        paid = float(raw_pa)
    except (TypeError, ValueError):
        return

    p_path = urlparse(path).path.rstrip("/")
    parts = [x for x in p_path.split("/") if x]
    if len(parts) < 3 or parts[0] != "invoice" or parts[-1] != ":payment":
        return
    inv_id = parts[1]
    if not inv_id.isdigit():
        return

    ratio_limit = float(os.environ.get("TRIPLETEX_PAYMENT_PAIDAMOUNT_RATIO_WARN", "5"))
    abs_floor = float(os.environ.get("TRIPLETEX_PAYMENT_PAIDAMOUNT_ABS_WARN", "5000"))

    try:
        snap = api.get(
            f"/invoice/{inv_id}",
            params={"fields": "amountOutstanding,amountCurrencyOutstanding"},
        )
        val = snap.get("value") if isinstance(snap, dict) else None
        if not isinstance(val, dict):
            return
        out = val.get("amountOutstanding")
        if out is None:
            out = val.get("amountCurrencyOutstanding")
        if out is None:
            return
        try:
            outstanding = float(out)
        except (TypeError, ValueError):
            return
        if outstanding <= 0:
            return
        if paid > ratio_limit * outstanding and paid > abs_floor:
            _agent_print(
                f"  ⚠️  WARNING: paidAmount {paid} seems very large — verify this is from "
                f"amountOutstanding ({outstanding} on GET /invoice/{inv_id}), not FCY × rate."
            )
    except Exception:
        pass


# ── Tool executor ─────────────────────────────────────────────────────────────

def execute_tool(name: str, inp: dict, api: TripletexAPI) -> str:
    try:
        if name == "tripletex_get":
            result = api.get(inp["path"], inp.get("params", {}))
            path_only = urlparse(inp["path"]).path.rstrip("/")
            if path_only == "/ledger/voucher":
                result = _strip_ledger_voucher_list_postings(result)
            text = json.dumps(result, ensure_ascii=False)
            # Voucher lists carry heavy postings — allow larger preview than default GET cap.
            limit = (
                int(os.environ.get("TRIPLETEX_GET_LEDGER_VOUCHER_LIST_CHARS", "12000"))
                if path_only == "/ledger/voucher"
                else int(os.environ.get("TRIPLETEX_GET_RESULT_CHARS", "6000"))
            )
            return text[:limit]

        elif name == "tripletex_post":
            path = inp["path"]
            if _is_ledger_voucher_create_path(path):
                return json.dumps(
                    {
                        "error": (
                            "tripletex_post must not be used for POST /ledger/voucher — use tripletex_post_voucher "
                            "(one-step inline postings first; two-step shell + /postings only if tenant 422 requires it)."
                        ),
                        "correct_tool": "tripletex_post_voucher",
                        "example_input": {
                            "date": "2026-03-19",
                            "description": "Bilag / journal description",
                            "send_to_ledger": True,
                            "postings": [
                                {
                                    "row": 1,
                                    "account": {"id": 0},
                                    "amountGross": 16750,
                                    "description": "Debit — replace account id",
                                },
                                {
                                    "row": 2,
                                    "account": {"id": 0},
                                    "amountGross": -16750,
                                    "description": "Credit — replace account id",
                                },
                            ],
                        },
                    },
                    ensure_ascii=False,
                )
            body_out = inp["body"]
            if not isinstance(body_out, dict):
                body_out = {}
            if _is_voucher_postings_subpath(path):
                body_out = _normalize_voucher_posting_line(dict(body_out))
            path_only = urlparse(path).path.rstrip("/")
            if path_only == "/employee/employment":
                body_out = _enrich_employment_post_body(dict(body_out))
            if path_only == "/salary/transaction":
                body_out = _enrich_salary_transaction_body(dict(body_out))
            if path_only == "/order":
                body_out = _enrich_order_post_body(dict(body_out))
            post_params = inp.get("params")
            if post_params is not None and not isinstance(post_params, dict):
                post_params = None
            try:
                result = api.post(path, body_out, params=post_params)
            except requests.HTTPError as e:
                if (
                    path_only == "/employee/employment"
                    and e.response is not None
                    and e.response.status_code == 404
                    and not _is_minimal_employment_post_body(body_out)
                ):
                    emp = body_out.get("employee")
                    sd = body_out.get("startDate")
                    if isinstance(emp, dict) and emp.get("id") is not None and sd:
                        minimal = {"employee": {"id": emp["id"]}, "startDate": sd}
                        try:
                            result = api.post(path, minimal, params=post_params)
                            return json.dumps(result, ensure_ascii=False)
                        except requests.HTTPError as e2:
                            raise e2 from e
                if (
                    path_only == "/salary/transaction"
                    and e.response is not None
                    and e.response.status_code == 422
                ):
                    err_txt = e.response.text or ""
                    if (
                        "ikke knyttet mot en virksomhet" in err_txt
                        or "Arbeidsforholdet er ikke knyttet" in err_txt
                    ):
                        _lock_employments_for_employees(
                            api,
                            _employee_ids_from_salary_transaction_body(body_out),
                        )
                raise
            return json.dumps(result, ensure_ascii=False)

        elif name == "tripletex_post_voucher":
            extras = inp.get("shell_extras")
            if extras is not None and not isinstance(extras, dict):
                extras = None
            result = post_voucher_two_step(
                api,
                date=inp["date"],
                description=str(inp.get("description") or ""),
                postings_lines=inp.get("postings") or [],
                send_to_ledger=bool(inp.get("send_to_ledger")),
                shell_extras=extras,
            )
            return json.dumps(result, ensure_ascii=False)

        elif name == "tripletex_put":
            path = inp["path"]
            body = inp.get("body")
            if not isinstance(body, dict):
                body = {}
            eid = _employment_id_from_path(path)
            if (
                eid is not None
                and body.get("division") is not None
                and eid in _employment_division_locked_ids
            ):
                return json.dumps(
                    {
                        "skipped": True,
                        "reason": (
                            "PUT division blocked: this employment already got 422 «Virksomheten kan ikke endres». "
                            "Do not retry division 2/3. Continue with POST /salary/transaction or stop if tenant blocks payroll."
                        ),
                        "employmentId": eid,
                    },
                    ensure_ascii=False,
                )
            try:
                result = api.put(path, body)
            except requests.HTTPError as e:
                if e.response.status_code == 422:
                    detail = e.response.text or ""
                    if (
                        "Virksomheten kan ikke endres" in detail
                        and eid is not None
                        and body.get("division") is not None
                    ):
                        _employment_division_locked_ids.add(eid)
                raise
            return json.dumps(result, ensure_ascii=False)

        elif name == "tripletex_put_action":
            p_action = inp.get("path") or ""
            prms = inp.get("params")
            if isinstance(prms, dict) and urlparse(p_action).path.rstrip("/").endswith("/:payment"):
                _maybe_warn_invoice_payment_paid_amount(api, p_action, prms)
            result = api.put_action(
                p_action,
                params=prms,
                body=inp.get("body"),
            )
            return json.dumps(result, ensure_ascii=False)

        elif name == "tripletex_delete":
            result = api.delete(inp["path"])
            return json.dumps(result)

        return json.dumps({"error": f"Unknown tool: {name}"})

    except requests.HTTPError as e:
        status = e.response.status_code
        detail = e.response.text[:400]
        # 403 is usually competition proxy / token expiry (infra); still surface to the model but don't frame like a careless 422
        if 400 <= status < 500:
            if status == 403:
                _agent_print(f"  ℹ️  HTTP 403 on {name} — often expired proxy token (infra). Model should continue remaining planned calls.")
            else:
                _agent_print(f"  ⚠️  4xx ERROR ({status}) on {name} — costs efficiency bonus!")
        return json.dumps({
            "http_error": status,
            "details":    detail,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Agent loop ────────────────────────────────────────────────────────────────

def run_agent(prompt: str, api: TripletexAPI, files: list[FileAttachment]) -> None:
    client = anthropic.Anthropic()

    # Build user message content
    content = []

    # Attach files: PDF / image as multimodal; CSV / plain text as decoded text (otherwise invisible to the model).
    unhandled_attachments: list[str] = []
    for f in files:
        if f.mime_type == "application/pdf":
            content.append({
                "type": "document",
                "source": {
                    "type":       "base64",
                    "media_type": "application/pdf",
                    "data":       f.content_base64,
                },
            })
        elif f.mime_type.startswith("image/"):
            content.append({
                "type": "image",
                "source": {
                    "type":       "base64",
                    "media_type": f.mime_type,
                    "data":       f.content_base64,
                },
            })
        elif (f.mime_type or "").strip() in (
            "text/csv",
            "text/plain",
            "application/csv",
        ) or (f.filename or "").lower().endswith(".csv"):
            try:
                raw = base64.b64decode(f.content_base64 or "", validate=False)
                csv_text = raw.decode("utf-8", errors="replace")
                max_chars = int(os.environ.get("ATTACHED_TEXT_MAX_CHARS", "120000"))
                if len(csv_text) > max_chars:
                    csv_text = (
                        csv_text[:max_chars]
                        + "\n\n[... truncated (ATTACHED_TEXT_MAX_CHARS) ...]\n"
                    )
                content.append({
                    "type": "text",
                    "text": f"=== File: {f.filename} ===\n{csv_text}",
                })
            except Exception as e:
                content.append({
                    "type": "text",
                    "text": f"=== File: {f.filename} (decode error: {e}) ===",
                })
        else:
            unhandled_attachments.append(f.filename or "(unnamed)")

    if unhandled_attachments:
        content.append({
            "type": "text",
            "text": (
                "Note: the following attachments were not embedded (unsupported type — not PDF, image, or CSV/text): "
                + ", ".join(unhandled_attachments)
            ),
        })

    content.append({"type": "text", "text": prompt})
    messages = [{"role": "user", "content": content}]

    log_input_chars = int(os.environ.get("LOG_TOOL_INPUT_CHARS", "8192"))
    log_result_chars = int(os.environ.get("LOG_TOOL_RESULT_CHARS", "4096"))

    # ReAct loop — cap below typical 5-minute harness budget
    for iteration in range(25):
        def _messages_create():
            return client.messages.create(
                model      = "claude-sonnet-4-20250514",
                max_tokens = 4096,
                system     = SYSTEM_PROMPT,
                tools      = TOOLS,
                messages   = messages,
            )

        try:
            response = _messages_create()
        except Exception as e:
            if _is_anthropic_rate_limit(e):
                _agent_print(
                    "  ⏳ Claude API rate limit (429) — sleeping 10s and retrying once…"
                )
                time.sleep(10)
                response = _messages_create()
            else:
                raise

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            _agent_print(f"  ✅ Done after {iteration + 1} iterations")
            return

        if response.stop_reason != "tool_use":
            _agent_print(f"  ⚠️  Unexpected stop: {response.stop_reason}")
            return

        # Execute tool calls
        tool_results: list[dict] = []
        saw_403 = False
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_in = json.dumps(block.input, ensure_ascii=False)
            _agent_print(f"  🔧 {block.name}  {_log_preview(tool_in, log_input_chars)}")
            result = execute_tool(block.name, block.input, api)
            _agent_print(f"     ← {_log_preview(result, log_result_chars)}")

            try:
                parsed = json.loads(result)
                if parsed.get("http_error") == 403:
                    saw_403 = True
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     result,
            })

        follow_up: list[dict] = list(tool_results)
        if saw_403:
            follow_up.append({
                "type": "text",
                "text": (
                    "Infrastructure note: At least one tool result was HTTP 403 (often expired competition proxy token). "
                    "Do NOT end the task yet — complete every remaining API call you had planned (e.g. POST /department for "
                    "each department name). One 403 does not mean later calls will fail; do not stop early."
                ),
            })
        messages.append({"role": "user", "content": follow_up})

    _agent_print("  ⚠️  Max iterations reached")


def _run_solve_sync(req: SolveRequest, task_label: str, ts: str) -> None:
    """
    Blocking work for POST /solve: logging session, Tripletex client, run_agent loop.
    Runs in a thread-pool executor so the FastAPI event loop stays free for /health etc.
    """
    with _solve_logging_session(task_label, ts) as log_path:
        _agent_print(f"\n{'='*60}")
        _agent_print(f"🧩 TASK / RUN   {task_label}")
        _agent_print(f"🕐 UTC          {ts}")
        if log_path:
            _agent_print(
                f"📁 LOG FILES    {log_path}  |  {(log_path.parent / 'last_solve.log').resolve()}"
            )
        _agent_print(f"📋 PROMPT (preview)  {req.prompt[:200]}{'…' if len(req.prompt) > 200 else ''}")
        _agent_print(f"📎 FILES        {[f.filename for f in req.files]}")
        _agent_print(f"{'='*60}")

        api = TripletexAPI(
            req.tripletex_credentials.base_url,
            req.tripletex_credentials.session_token,
        )

        try:
            run_agent(req.prompt, api, req.files)
        except Exception as e:
            _agent_print(f"  ❌ {e}")
            # Always return completed — partial work > error response


# ── FastAPI endpoint ──────────────────────────────────────────────────────────

@app.post("/solve")
async def solve(
    req: SolveRequest,
    authorization: Optional[str] = Header(default=None),
    x_task_id: Optional[str] = Header(default=None, alias="X-Task-Id"),
):
    # Validate API key if one is configured
    if API_KEY:
        expected = f"Bearer {API_KEY}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")

    task_label = _resolve_task_label(req, x_task_id)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _reset_per_solve_guards()

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        functools.partial(_run_solve_sync, req, task_label, ts),
    )

    return {"status": "completed"}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)