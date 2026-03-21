"""
Tripletex AI Accounting Agent — NM i AI 2026
Endpoint: POST /solve
"""

import os
import sys
import json
import re
import contextvars
from contextlib import contextmanager
from pathlib import Path
import requests
import anthropic
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
    Bind virksomhet at **POST /employee/employment** when possible — some tenants reject **PUT** division later
    («Virksomheten kan ikke endres») but accept **division** on create.
    """
    b = dict(body)
    if b.get("division") is not None:
        return b
    if not isinstance(b.get("employee"), dict) or b.get("employee", {}).get("id") is None:
        return b
    if not b.get("startDate") or not b.get("taxDeductionCode"):
        return b
    b["division"] = {"id": 1}
    return b


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
    """True for POST /ledger/voucher (optional ?query), not sub-resources."""
    path_only = urlparse(path).path
    return path_only.rstrip("/") == "/ledger/voucher"


def _is_voucher_postings_subpath(path: str) -> bool:
    parts = [p for p in urlparse(path).path.split("/") if p]
    return (
        len(parts) >= 4
        and parts[0] == "ledger"
        and parts[1] == "voucher"
        and parts[-1] == "postings"
    )


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
    """Map legacy freeAccountingDimension* to accountingDimensionValues for /postings."""
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
        return out
    existing = out.get("accountingDimensionValues")
    if isinstance(existing, list):
        out["accountingDimensionValues"] = list(existing) + extras
    else:
        out["accountingDimensionValues"] = extras
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
            flipped = {**line_body, "amountGross": -float(ag)}
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
    Step 1: POST /ledger/voucher?sendToLedger=false with {date, description, postings: []}.
    Step 2: POST /ledger/voucher/{id}/postings once per line.
    Step 3 (optional): PUT /ledger/voucher/{id}/:sendToLedger if send_to_ledger.
    """
    shell: dict[str, Any] = {
        "date": date,
        "description": description,
        "postings": [],
    }
    if shell_extras:
        for k, v in shell_extras.items():
            if k != "postings":
                shell[k] = v

    # Empty shell + sendToLedger=true → 422 «Et bilag kan ikke registreres uten posteringer».
    # OpenAPI may default sendToLedger on POST — force false until lines exist.
    v_path = "/ledger/voucher?sendToLedger=false"

    voucher_json = api.post(v_path, shell)
    vid = _voucher_id_from_create_response(voucher_json)
    if vid is None:
        return {
            "error": "Could not read voucher id from create response; aborting posting lines.",
            "voucher_response": voucher_json,
            "postings_skipped": postings_lines,
        }

    line_results: list[Any] = []
    for row_idx, p in enumerate(postings_lines):
        if not isinstance(p, dict):
            line_results.append({"http_error": 400, "details": "posting is not an object"})
            continue
        line_body = _normalize_voucher_posting_line(dict(p))
        if "row" not in line_body:
            line_body = {**line_body, "row": row_idx + 1}
        line_results.append(_post_voucher_line(api, vid, line_body, row_idx))

    out: dict[str, Any] = {
        "voucher": voucher_json,
        "voucherId": vid,
        "postingResponses": line_results,
    }
    if send_to_ledger:
        if _voucher_line_results_have_http_error(line_results):
            out["sendToLedger_skipped"] = "one or more posting lines failed"
        else:
            try:
                out["sendToLedgerResponse"] = _send_voucher_to_ledger(api, vid)
            except requests.HTTPError as e:
                out["sendToLedger_http_error"] = e.response.status_code
                body = e.response.text or ""
                out["sendToLedger_details"] = body[:800]
    return out


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
            "Common paths: /employee, /employee/employment (e.g. **?employeeId=X&fields=id,startDate,division** or **/{id}?fields=id,division**), /customer, /product, /activity, /invoice, /invoice/paymentType, /order, "
            "/project, /project/hourlyRates, /timesheet/entry, /department (list or fetch; create via POST with {name}), /travelExpense, "
            "/travelExpense/costCategory, /travelExpense/costCategory/{id}, /travelExpense/paymentType (**`fields=id`** only — **not** **name**), /ledger/account, /ledger/voucher, "
            "/ledger/accountingDimensionName, /ledger/accountingDimensionValue, /ledger/vatType\n"
            "**GET /ledger/vatType:** skip a **full** list when the task only needs **standard Norwegian outgoing** rates on **invoice order lines** — put **`vatType: {id}`** on **each** **`orderLines[]`** per **SYSTEM_PROMPT** (**25→3**, **15→31**, **12→32**, **0→6**). Use **GET /ledger/vatType** only for **non-standard** rates or after **POST /order** **422** on VAT.\n"
            "List responses are wrapped: {fullResultSize: N, values: [...]}\n"
            "Use ?fields=id,name,* to limit fields. Use ?from=0&count=100 for pagination.\n"
            "GET /ledger/account **1920** (**only** when you will **`PUT /order/.../:invoice`** or prompt requires invoice bank — see SYSTEM_PROMPT **WHEN TO SKIP**): **`number=1920`**, **`fields=id,number,bankAccountNumber`** → **`PUT /ledger/account/{id}`** **`{bankAccountNumber: ...}`**. **Do not** **POST** **1921**. **Do not** fetch 1920 for project-only / timesheet / travel-only tasks.\n"
            "GET /product (list): query **`name`** (substring / \"Containing\") and/or **`productNumber`** (when you have the SKU); "
            "use **`fields=id,name,number`** (or minimal set) — **before** POST /product, look up existing rows and **reuse** **id**.\n"
            "GET /ledger/voucher (list/search): requires **dateFrom** and **dateTo**; **dateTo** must be strictly after **dateFrom**.\n"
            "CRITICAL: path /invoice (no id) = list endpoint. Params MUST include invoiceDateFrom AND invoiceDateTo "
            "(YYYY-MM-DD) every time — even alongside customerId, fields, pagination. Missing dates → 422. "
            "Example: {customerId: X, invoiceDateFrom: '2000-01-01', invoiceDateTo: '2099-12-31', fields: 'id,invoiceNumber,invoiceDate,amountExcludingVat'}. "
            "Do NOT request isPaid, dueDate, amountIncludingVat, or paid — they are not valid invoice list fields.\n"
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
            "/project, /project/hourlyRates, /timesheet/entry, /department (POST {name} — **one POST per department**), /travelExpense, "
            "/travelExpense/perDiemCompensation, /travelExpense/cost, /ledger/accountingDimensionName ({dimensionName}), "
            "/ledger/accountingDimensionValue ({displayName})\n"
            "**Ledger / journal vouchers:** use **`tripletex_post_voucher`** — **not** **`tripletex_post`** on **`/ledger/voucher`** (see that tool).\n"
            "POST /customer: include **email** (and phone, org number) in body whenever the user prompt mentions them — "
            "omitting stated email breaks automated checks.\n"
            "POST /product: **priceExcludingVatCurrency** (not \"price\" — 422). **vatType**: **outgoing** sales code for the product’s **default**; **not** incoming/fradrag **id 1**. For **invoices with different VAT % per line**, set **`vatType: {id}`** on **each** **`orderLines[]`** entry (see SYSTEM_PROMPT — do **not** rely on product default alone). Travel costs use different vat rules — SYSTEM_PROMPT. "
            "**Before** POST, **GET /product** with **`name`** and/or **`productNumber`** + **`fields`** — if a row matches, **reuse** **id** (no POST). "
            "If **422** **«Produktnummeret … er i bruk»** or **«Produktnavnet … er allerede registrert»**, **do not** burn calls on invented POST bodies — **GET /product**, **reuse** existing **id**; only if still no row, **retry POST** **without** **`number`** (duplicate-number case).\n"
            "POST /department: body **{name: \"...\"}** — multiple departments → **separate POST** per name (see SYSTEM_PROMPT).\n"
            "POST /employee: requires **userType** (e.g. STANDARD) and **department: {id}**; use **POST /employee/employment** for startDate / tax — not nested employmentDetails on /employee.\n"
            "Payroll: **before** **POST /employee/employment**, **GET /employee/{id}?fields=dateOfBirth** — if **null**, **PUT /employee/{id}** **`{dateOfBirth: …}`**. **POST /employee/employment** should include **`division: {id: 1}`** (auto-added if missing) — **PUT** division later may return **422** *«Virksomheten kan ikke endres»*. **GET /employee/employment/{id}?fields=id,division**; if **division** null and **PUT** allowed, try **1**; **403** → **2**, **3**; **422** → **stop** (see SYSTEM_PROMPT).\n"
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
            "Register payment: **one** **PUT** `/invoice/{id}/:payment` with query params only — **tripletex_put_action**. "
            "**paidAmount** = full amount **including VAT** (see SYSTEM_PROMPT); never call payment twice.\n"
            "**POST /employee/employment** (payroll): **`division: {id: 1}`** is added when missing — **virksomhet** at create; some tenants **block** later **PUT** division.\n"
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
            "**Always use this for ledger / journal vouchers** (manual postings). Implements the required **two-step** Tripletex flow:\n"
            "1) **POST /ledger/voucher** with **`{date, description, postings: []}`**\n"
            "2) **POST /ledger/voucher/{id}/postings** once **per line** with a **single posting JSON object** (not one HTTP request with a JSON array)— e.g. "
            "**`row`**, **`account: {id}`**, **`amountGross`** (debit **positive**, credit **negative**), optional **`description`**, **`accountingDimensionValues`**.\n"
            "On **422** for a line, the implementation **retries once** with **negated** **`amountGross`** and prints the first error to the log.\n"
            "Set **`send_to_ledger`: true** to **POST** shell with **`sendToLedger=false`**, add lines, then **`PUT /ledger/voucher/{id}/:sendToLedger`** — **never** **`?sendToLedger=true`** on the empty shell (422 *«…uten posteringer»*). "
            "Optional **`shell_extras`** merges extra **Voucher** fields (not **postings**). "
            "Returns **`voucher`**, **`voucherId`**, **`postingResponses`**, and **`sendToLedgerResponse`** (or error keys) when applicable."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Voucher date YYYY-MM-DD"},
                "description": {"type": "string", "description": "Voucher description"},
                "postings": {
                    "type": "array",
                    "description": "Line objects; each is POSTed separately to /ledger/voucher/{id}/postings",
                    "items": {"type": "object"},
                },
                "send_to_ledger": {
                    "type": "boolean",
                    "description": "If true, after all lines POST, PUT /ledger/voucher/{id}/:sendToLedger (not on empty shell)",
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
            "Register payment: **PUT** `/invoice/{id}/:payment` — **required query params**: paymentDate, paymentTypeId, **paidAmount** (incl. VAT); "
            "optional paidAmountCurrency. **Single call only.** Omit body unless Swagger requires JSON.\n"
            "**Reverse a payment** (bank return / undo payment): after **`GET /invoice/{id}`** (see **`postings`**), **`PUT /ledger/voucher/{paymentVoucherId}/:reverse`** with **params** **`{date: 'YYYY-MM-DD'}`** — **not** `:createCreditNote` (that credits the **sale**).\n"
            "Credit note (cancel sale): PUT /invoice/{invoiceId}/:createCreditNote (query params per Swagger).\n"
            "Pass the full path including /:invoice, /:payment, /:createCreditNote, or /ledger/voucher/{id}/:reverse. Use params and/or body as required by the action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Full path with action, e.g. /order/123/:invoice, /invoice/456/:payment, /invoice/456/:createCreditNote, /ledger/voucher/789/:reverse",
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

LANGUAGES: Tasks arrive in Norwegian, Nynorsk, English, German, French, Spanish, or Portuguese. Understand fully before acting.

SCORING — THIS MATTERS:
- You are scored on CORRECTNESS (field-by-field checks) and EFFICIENCY (API call count + zero 4xx errors)
- Efficiency bonus ONLY applies if you achieve 100% correctness — so correctness comes first
- Every 4xx error reduces your efficiency score — do NOT make speculative or trial-and-error calls
- **Do not** finish with **no API calls** when the user asked you to change data in Tripletex — unknown task types still require **GET-then-POST** attempts (see **Custom accounting dimensions & ledger**)
- Plan your full call sequence before making the first request

PLANNING RULE: Before any API call, think through:
1. What is the task asking for exactly?
2. What data do I already have from the prompt?
3. What IDs or lookups do I need first?
4. What is the minimum sequence of calls to complete this correctly?

TRIPLETEX API KNOWLEDGE:
- Paths: /employee, /employee/employment (employment **`id`**; **GET** **`?employeeId=`** list; **PUT** with **`division: {id}`**), /customer, /product, /activity, /order, /invoice, /invoice/paymentType (**`fields=id`** only), /project, /project/hourlyRates, /timesheet/entry, /project/orderline ([BETA] project lines), /department, /travelExpense, /travelExpense/costCategory (+ **`/{id}`** for details), /travelExpense/paymentType (**`fields=id`** only), /travelExpense/perDiemCompensation, /travelExpense/cost, /salary/transaction, /salary/type, /salary/payslip (read-only), /salary/compilation (read-only), /ledger/account, **manual journals: `tripletex_post_voucher` tool** (wraps **`/ledger/voucher`** + **`/ledger/voucher/{id}/postings`**), /ledger/accountingDimensionName, /ledger/accountingDimensionValue (custom dimensions), /ledger/vatType (VAT ids for products) — **avoid** **`GET /company/divisions`** for payroll setup (often **403** in competition)
- Action URLs use a /:actionName segment (e.g. /:invoice, /:createCreditNote, **/ledger/voucher/{id}/:reverse**) — call these with tripletex_put_action (PUT), not as a normal field update on tripletex_put
- Dates: "YYYY-MM-DD" format always
- List responses: {"fullResultSize": N, "values": [...]}
- GET /invoice (listing or searching invoices): **required query params** **invoiceDateFrom** and **invoiceDateTo** (YYYY-MM-DD). **This is mandatory even if you add other filters** (customerId, fields, from, count, etc.) — Tripletex still returns **422** (“Kan ikke være null”) if either date is missing. Use a wide window when needed (e.g. 2000-01-01 through 2099-12-31). Only **GET /invoice/{numericId}** for one invoice skips this rule
- Invoice list **fields** (confirmed): **id**, **invoiceNumber**, **invoiceDate**, **amountExcludingVat** — do **not** request **isPaid**, **dueDate**, **amountIncludingVat**, or **paid** (not on InvoiceDTO for this list)
- **Products:** before **`POST /product`**, **`GET /product`** with **`name`** (and **`productNumber`** if the task has a SKU) + **`fields=id,name,number`** — reuse **`id`** when the row matches; on **422** duplicate name/number messages, **GET** and reuse — do not spam **`POST /product`**
- POST /order: **deliveryDate** is **required** (422 if null). If the prompt does not give a delivery date, use the same **YYYY-MM-DD** as **orderDate**
- **Order lines + VAT (invoices):** **`orderLines[]`** supports **`vatType: {id}`** (same **outgoing** sales codes as products). If the task gives **different VAT % per line**, set **`vatType` on every line** from the task — **do not** assume the **product** master **vatType** matches each line. **Standard Norwegian outgoing** **id** shortcut (competition — **skip GET /ledger/vatType** unless the rate is unusual or **POST /order** **422**): **25% → 3**, **15% → 31**, **12% → 32**, **0% → 6**. **Efficiency:** after **PUT /order/{id}/:invoice** returns **success**, **do not** **GET /invoice/{id}** only to re-read line VAT unless the prompt explicitly asks for verification or payment amounts you cannot compute.
- Auth: already handled — just make the calls
- **403 "Invalid or expired token"** mid-session: usually **competition proxy** / expired **session_token** — **infrastructure**, not a broken request body. **Do not** abandon the run after **one** 403: **still execute your remaining planned tool calls** (e.g. **POST /department** for **each** name you intended, or retry the sequence) — the token may succeed on **other** endpoints or moments. **Never** `end_turn` early **only** because a single call returned 403. A **fresh** `session_token` is required for a clean run; if 403 is **frequent**, raise it with **organisers** (not fully fixable in agent code).
- **Company bank account for invoicing:** **`Company`** has **no** bank fields in the API. **`GET /ledger/account?number=1920&fields=id,number,bankAccountNumber`** → **`PUT /ledger/account/{id}`** **`{bankAccountNumber: \"86011117947\"}`** — **not** **`POST` 1921**.

COMMON TASK PATTERNS (memorize these to avoid extra calls):

Invoice bank registration: use **MANDATORY SETUP BEFORE INVOICE TASKS** at the **top** — **`GET`** **`number=1920`** → **one** **`PUT`** **`bankAccountNumber`** only; **no** **`POST /ledger/account` 1921**; **no** duplicate **`bankAccountNumber`** across accounts; **no** random kontonummer guesses.

Create department:
POST /department {name: "..."}
CRITICAL: Several departments → **one POST /department per department** (separate bodies), not one call with an array.

Create employee:
Step 1 — GET /department?fields=id,name to find department id (need this first)
Step 2 — POST /employee {
  firstName,
  lastName,
  email,
  dateOfBirth,          # if given, format "YYYY-MM-DD"
  userType: "STANDARD", # REQUIRED — always include this
  department: {id: X}   # REQUIRED — use id from step 1
}
Step 3 — **POST /employee/employment** when the task needs employment (incl. **startDate** in prompt) **or any payroll / lønn / salary work** (if prompt omits date, use **`"2026-01-01"`** or task-aligned date):
**Before** this POST for an **already existing** employee (e.g. resolved via **GET /employee?email=**): **tripletex_get** **`GET /employee/{id}?fields=id,dateOfBirth`**. If **`dateOfBirth`** is **null**, **tripletex_put** **`PUT /employee/{id}`** with **`{"dateOfBirth": "YYYY-MM-DD"}`** — use the **prompt** birth date if stated; if the task gives **no** birth date, use **`1990-01-01`** **once** (API valid date placeholder for tenants that require DOB before employment). **Skipping this** often causes **422** *«employee.dateOfBirth»* / *«Feltet må fylles ut»* on **POST /employee/employment**.
POST /employee/employment {
  employee: {id: newEmployeeId},
  startDate: "YYYY-MM-DD",
  isMainEmployer: true,
  taxDeductionCode: "loennFraHovedarbeidsgiver",
  division: {id: 1}   # optional in JSON — **tripletex_post** auto-adds **`division: {id: 1}`** if missing (binds **virksomhet** at create; some tenants **reject** later **PUT** division with *«Virksomheten kan ikke endres»*)
}
Note the returned employment **`id`** (or **GET** **`/employee/employment?employeeId=newEmployeeId&fields=id,startDate,division`** to read the row).

Step 4 — **Link employment → division (virksomhet)** — often needed for **`POST /salary/transaction`**; error *«Arbeidsforholdet er ikke knyttet mot en virksomhet»* means **`division`** was never set:
- **Do not** rely on **`GET /company/divisions`** — it often returns **403** in competition; **do not** stall the run on it.
- **tripletex_get** **`GET /employee/employment/{employmentId}?fields=id,division`**.
- If **`division`** is **null** / missing: **tripletex_put** **`PUT /employee/employment/{employmentId}`** body **`{"division": {"id: 1}}`**. **Log** success or error. On **403**, retry **`{"division": {"id": 2}}`**, then **`{"division": {"id": 3}}`**, logging each attempt.
- **422** *«Virksomheten kan ikke endres»* on **any** **`PUT`** with **`division`**: **stop immediately** — **never** send **`PUT`** with **`division` 2** or **3** (or any other id) for **this** **`employmentId`** in the same run. The runtime may **block** further division **PUT**s after the first such **422**. **Continue** with **`POST /salary/transaction`** (tenant may still accept payroll); if **that** fails on **virksomhet**, it is likely **sandbox/company setup**, not missing retries.
- **COMPETITION SHORTCUT:** **`division.id: 1`** often works when **PUT** is allowed — try **`1`** first once; after a **200**/**204**, **reuse** that **`id`** for other employments where **PUT** is allowed.

CRITICAL: Do NOT put employmentDetails inside POST /employee body — field does not exist.
CRITICAL: userType and department.id are required — POST will fail without them.
→ If task asks for administrator / kontoadministrator: PUT /employee/{id} with {"administrator": true} after create (per Swagger).

Create customer / supplier:
POST /customer {
  name,
  isCustomer: true,      # for customers
  isSupplier: true,      # for suppliers
  email,                 # ALWAYS include if mentioned in prompt
  organizationNumber,    # ALWAYS include if mentioned in prompt
  phoneNumber            # include if mentioned
}
CRITICAL: Never omit email or organizationNumber if they appear in the prompt.

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

Search for invoice (list) — also used before payment:
GET /invoice?customerId=X&invoiceDateFrom=2000-01-01&invoiceDateTo=2099-12-31
  &fields=id,invoiceNumber,invoiceDate,amountExcludingVat
WARNING: isPaid, dueDate, amountIncludingVat, paid do NOT exist — never request them in fields.

Register payment on invoice:
Get payment types:
GET /invoice/paymentType?fields=id   # `name` field does NOT exist here
Use first id from results as paymentTypeId

1. GET /invoice?customerId=X&invoiceDateFrom=2000-01-01&invoiceDateTo=2099-12-31
     &fields=id,invoiceNumber,invoiceDate,amountExcludingVat → invoice **id** (match row to task)
2. GET /invoice/paymentType?fields=id → **paymentTypeId** (use first id if task does not name one). **Never request `name`** here — `name` is not a valid field and causes 4xx.
3. tripletex_put_action: **PUT** `/invoice/{id}/:payment` with **params** only:
     paymentDate=YYYY-MM-DD&paymentTypeId=X&paidAmount=AMOUNT
CRITICAL: **paidAmount** must be the **full cash amount including VAT**. If the API only returns **amountExcludingVat** and VAT is **25%**, use **paidAmount = amountExcludingVat × 1.25** (adjust multiplier if the task states another VAT rate or use **paidAmountCurrency** / Swagger for special cases).
CRITICAL: Call **PUT /invoice/{id}/:payment exactly ONCE** for full settlement — **never** pay twice, retry with a second amount, or split into ex-VAT + VAT as two calls.

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

Log hours (timesheet entry):
GET /employee?email=X&fields=id,firstName,lastName → employee id
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

Create ledger voucher (bilagsføring) — **always** call **`tripletex_post_voucher`** (**never** **`tripletex_post`** on **`/ledger/voucher`** — the tool is blocked). Swagger [v2-docs](https://tripletex.no/v2-docs/) **Voucher** uses **`postings`** (plural) only — **not** **`posting`** / **`rows`**. The helper runs the **two-step** flow for you:
1. **POST /ledger/voucher?sendToLedger=false** with **`{"date": "YYYY-MM-DD", "description": "...", "postings": []}`** — **`sendToLedger=true` on this step with empty postings → 422** (*«Et bilag kan ikke registreres uten posteringer»*).
2. **POST /ledger/voucher/{id}/postings** once **per line** with a **single object** each time, e.g.:
   **`{"row": 1, "account": {"id": X}, "amountGross": 16750, "description": "..."}`** and **`{"row": 2, "account": {"id": Y}, "amountGross": -16750, "description": "..."}`** — pass the **same** line objects in the **`postings`** array of **`tripletex_post_voucher`**; the server sends **one HTTP POST per element** (do **not** try one request with a JSON array body to **`/postings`** unless Swagger says otherwise).
3. If **`send_to_ledger: true`**: **`PUT /ledger/voucher/{id}/:sendToLedger`** (tool does this after lines succeed).
- **`row`**: **1-based** line numbers; omitted rows get **`row`** auto-filled in order.
- **422** on a line: the tool **logs the exact error** and **retries once** with **negated** **`amountGross`** (correct **debit +** / **credit −**).

CRITICAL: **amountGross** — **positive = debit**, **negative = credit**; **NOT** debit, credit, debitAmount, creditAmount (alternate: **`amount`** per OpenAPI).
CRITICAL: All lines must **balance** (sum of **amountGross** = 0).
**Document import** (only when the task is a **file**): **POST /ledger/voucher/importDocument** — **multipart/form-data** **`file`** — **not** **`tripletex_post_voucher`**.

If **POST /ledger/voucher/{id}/postings** returns **404**, re-check the tenant **openapi.json** — some snapshots omit that sub-resource.

Search vouchers:
GET /ledger/voucher?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD&fields=id,postings
CRITICAL: **dateFrom** and **dateTo** are required. **dateTo** must be **strictly after** **dateFrom**.

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
- If **`values`** is **empty** / no row: **POST /employee/employment** with **`employee: {id}`**, **`startDate`**, **`isMainEmployer: true`**, **`taxDeductionCode: "loennFraHovedarbeidsgiver"`** — **`tripletex_post`** injects **`division: {id: 1}`** if omitted (prefer **virksomhet** at **create**). Then run **Create employee Step 4** only if **`division`** is still **null** after **GET** (**PUT** **`1`**; **403** → **2**, **3**; **422** *«Virksomheten…»* → **stop**).
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


# ── Tool executor ─────────────────────────────────────────────────────────────

def execute_tool(name: str, inp: dict, api: TripletexAPI) -> str:
    try:
        if name == "tripletex_get":
            result = api.get(inp["path"], inp.get("params", {}))
            text = json.dumps(result, ensure_ascii=False)
            return text[:6000]   # Truncate large list responses

        elif name == "tripletex_post":
            path = inp["path"]
            if _is_ledger_voucher_create_path(path):
                return json.dumps(
                    {
                        "error": (
                            "tripletex_post must not be used for POST /ledger/voucher — use tripletex_post_voucher "
                            "(two-step shell + one POST per line to /ledger/voucher/{id}/postings)."
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
                if path_only == "/salary/transaction" and e.response.status_code == 422:
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
            result = api.put_action(
                inp["path"],
                params=inp.get("params"),
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

    # Attach files (PDFs / images) as multimodal content
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

    content.append({"type": "text", "text": prompt})
    messages = [{"role": "user", "content": content}]

    log_input_chars = int(os.environ.get("LOG_TOOL_INPUT_CHARS", "8192"))
    log_result_chars = int(os.environ.get("LOG_TOOL_RESULT_CHARS", "4096"))

    # ReAct loop — max 12 iterations, well within 5-minute timeout
    for iteration in range(20):
        response = client.messages.create(
            model      = "claude-sonnet-4-20250514",
            max_tokens = 4096,
            system     = SYSTEM_PROMPT,
            tools      = TOOLS,
            messages   = messages,
        )

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

    return {"status": "completed"}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)