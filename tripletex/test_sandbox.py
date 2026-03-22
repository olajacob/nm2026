#!/usr/bin/env python3
"""Tripletex sandbox smoke tests (no LLM). Run before full /solve runs.

  export TRIPLETEX_SESSION_TOKEN='<base64 session token>'  # Basic password, user 0
  export TRIPLETEX_BASE_URL='https://kkpqfuj-amager.tripletex.dev/v2'   # optional

  Or put TRIPLETEX_SESSION_TOKEN in ../.env (nmai2026/.env) — loaded automatically if unset.

  python test_sandbox.py              # agent unit tests + Tripletex checks
  python test_sandbox.py --local-only # agent unit tests only (no token)
  python test_sandbox.py --health-url http://127.0.0.1:8080   # + agent GET /health
  python test_sandbox.py --supplier-invoice                 # + POST /customer + POST /supplierInvoice (WARN+skip if tenant returns only HTTP 500 code 1000)
  python test_sandbox.py --voucher                          # + post_voucher_two_step (2-line journal)
  python test_sandbox.py --voucher-vat                      # + 3-line MVA (7300/2710/2400, overstyres via env)
  python test_sandbox.py --ledger-probe                     # + ledger list/detail/posting/project (NM ledger flow)
  python test_sandbox.py --invoice-fee-probe              # + invoice list fields sanitize, AR voucher+customer, accounts, outstanding
  python test_sandbox.py --month-end-probe               # + voucher detail fields bare `postings` (no 400) + GL 1710/1290/6010
  python test_sandbox.py --dimension-voucher           # + accountingDimensionName/Value + 2-line bilag m/accountingDimensionValues (Task ~06)

  Agent unit tests (no HTTP): always run first — employee email guard, PDF hints (HR + supplier invoice), supplierInvoice 500/1000 + duplicate block.

  Env (optional): TRIPLETEX_GET_CACHE=0 disables per-/solve GET cache for static paths (ledger account, payment types, vat types).
  TRIPLETEX_DEFAULT_TASK_ID — log label when JSON/header/env task id omitted (e.g. 11 for grep).
  TRIPLETEX_PAYMENT_PAIDAMOUNT_ACTION=warn|clamp|block (default clamp); TRIPLETEX_VOUCHER_ALLOW_BANK_LINES=1 for probes using 1920.
  TRIPLETEX_VOUCHER_DEBIT_NUMBER, TRIPLETEX_VOUCHER_CREDIT_NUMBER (default credit 2900 — not 1920; bank lines blocked on tripletex_post_voucher);
  for --voucher-vat: TRIPLETEX_VOUCHER_EXPENSE_NUMBER, TRIPLETEX_VOUCHER_VAT_NUMBER, TRIPLETEX_VOUCHER_AP_NUMBER;
  optional TRIPLETEX_VOUCHER_SUPPLIER_ID (else first isSupplier from GET /customer).
  for --ledger-probe seed journal: TRIPLETEX_LEDGER_PROBE_DEBIT_NUMBER (default 6800), TRIPLETEX_LEDGER_PROBE_CREDIT_NUMBER (default 2900).
  for --dimension-voucher: TRIPLETEX_DIM_VOUCHER_DEBIT_NUMBER (default 6800), TRIPLETEX_DIM_VOUCHER_CREDIT_NUMBER (default 2900).
"""

from __future__ import annotations

import warnings
from typing import Any, Optional

# Before importing agent → requests → urllib3 (LibreSSL on macOS spams this at urllib3 import).
warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL")

import argparse
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import requests

from agent import (
    FileAttachment,
    SolveRequest,
    TripletexAPI,
    TripletexCredentials,
    build_dynamic_system_prompt,
    extract_prompt_structured_hints,
    infer_task_family,
    _parse_tool_body_object,
    _apply_invoice_payment_paid_amount_guard,
    _apply_tripletex_get_sanitizers,
    _employment_division_put_rejected,
    _employment_division_post_ids,
    _employment_post_attempt_sequence,
    _enrich_travel_expense_post_body,
    _salary_transaction_422_tool_note,
    _reset_per_solve_guards,
    _get_params_cache_key,
    _ledger_account_3400_fee_credit_quirk_note,
    _merge_customer_into_positive_posting_lines,
    _nmiai_proxy_expired_token_detail,
    _normalize_voucher_posting_line,
    _reject_manual_voucher_bank_lines,
    _pdf_employee_context_hint,
    _pdf_supplier_invoice_context_hint,
    COMPETITION_BASE_URL,
    _supplier_party_id_from_postings,
    _resolve_task_label,
    _tripletex_get_path_is_session_cacheable,
    execute_tool,
    post_voucher_two_step,
)


def _load_dotenv_from_repo() -> None:
    """Set os.environ from first existing .env (does not override already-set vars)."""
    here = Path(__file__).resolve().parent
    for env_path in (here.parent / ".env", here / ".env"):
        if not env_path.is_file():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
        return


def _ok(msg: str) -> None:
    print(f"  OK  {msg}", flush=True)


def _fail(msg: str) -> None:
    print(f"  FAIL {msg}", file=sys.stderr, flush=True)


class _ApiMustNotGet:
    """Stub TripletexAPI for execute_tool paths that must return before HTTP."""

    def __init__(self) -> None:
        self.supplier_invoice_500_seen: set[str] = set()

    def get(self, *_a, **_k) -> dict:
        raise AssertionError("api.get must not be called (guard should short-circuit)")


def run_agent_unit_tests() -> int:
    """
    Local regression: employee list empty-email rejection, PDF+HR hint wiring.
    No TRIPLETEX_SESSION_TOKEN or network required.
    """
    err = 0
    dead = _ApiMustNotGet()

    if not _nmiai_proxy_expired_token_detail('{"source":"nmiai-proxy","error":"Invalid or expired"}'):
        _fail("nmiai proxy detail helper: expected True for proxy JSON")
        err += 1
    elif not _nmiai_proxy_expired_token_detail("invalid or expired proxy token"):
        _fail("nmiai proxy detail helper: expected True for plain message")
        err += 1
    elif _nmiai_proxy_expired_token_detail(""):
        _fail("nmiai proxy detail helper: expected False for empty")
        err += 1
    else:
        _ok("_nmiai_proxy_expired_token_detail (local)")

    if infer_task_family("=== File: bank.csv ===\nAmount;Ref") != "bank_csv":
        _fail("infer_task_family: expected bank_csv for === File: CSV block")
        err += 1
    else:
        _ok("infer_task_family bank_csv (local)")

    if infer_task_family("Korriger feil i bilagene i januar") != "ledger_audit":
        _fail("infer_task_family: expected ledger_audit for feil i bilag")
        err += 1
    else:
        _ok("infer_task_family ledger_audit (local)")

    if infer_task_family('Opprett produktet "X" med pris 100 kr') != "default":
        _fail("infer_task_family: product create should stay default family")
        err += 1
    else:
        _ok("infer_task_family default for product-style prompt (local)")

    if infer_task_family("Gjennomfør heile prosjektsyklusen for Dataplattform Skogheim") != "project_cycle":
        _fail("infer_task_family: Nynorsk prosjektsyklus should be project_cycle (not supplier_inv)")
        err += 1
    else:
        _ok("infer_task_family project_cycle — prosjektsyklus (local)")

    if infer_task_family("Registrer timar på prosjekt og send faktura til kunde") != "project_cycle":
        _fail("infer_task_family: timar + prosjekt/faktura → project_cycle")
        err += 1
    else:
        _ok("infer_task_family project_cycle — timar + faktura (local)")

    _dyn = build_dynamic_system_prompt("test")
    if "PRIORITIZED MODE FOR THIS REQUEST" not in _dyn or "(family:" not in _dyn:
        _fail("build_dynamic_system_prompt: expected router section appended")
        err += 1
    else:
        _ok("build_dynamic_system_prompt includes PRIORITIZED MODE (local)")

    _hints = extract_prompt_structured_hints("Fra 2026-03-15 betal faktura — 12 500,50 kr og konto 1920")
    if "2026-03-15" not in _hints or "12 500,50 kr" not in _hints.lower():
        _fail(f"extract_prompt_structured_hints: expected date + amount, got {_hints[:200]!r}")
        err += 1
    else:
        _ok("extract_prompt_structured_hints date + NOK amount (local)")

    _pb, _pe = _parse_tool_body_object(
        '{"travelExpense": {"id": 111}, "amountCurrencyIncVat": 4100, "paymentType": {"id": 41}}'
    )
    if _pe or _pb.get("amountCurrencyIncVat") != 4100:
        _fail(f"_parse_tool_body_object JSON string: err={_pe!r} d={_pb!r}")
        err += 1
    else:
        _ok("_parse_tool_body_object parses JSON string to dict (local)")

    _pb2, _pe2 = _parse_tool_body_object("[1,2]")
    if _pe2 is None or "object" not in _pe2.lower():
        _fail(f"_parse_tool_body_object array should error, got {_pe2!r}")
        err += 1
    else:
        _ok("_parse_tool_body_object rejects JSON array (local)")

    try:
        _b_te = _enrich_travel_expense_post_body(
            {
                "employee": {"id": 1},
                "paymentType": {"id": 99},
                "type": "TRAVEL",
                "travelDetails": {"purpose": "x"},
            }
        )
        if "paymentType" in _b_te or _b_te.get("type") != 0:
            _fail(f"travel expense POST enrich: expected paymentType stripped + type 0, got {_b_te!r}")
            err += 1
        else:
            _ok("POST /travelExpense enrich: strip paymentType, TRAVEL→0 (local)")
    except Exception as e:
        _fail(f"travel expense POST enrich: {e}")
        err += 1

    _sn = _salary_transaction_422_tool_note(
        422,
        '{"validationMessages":[{"message":"Ugyldig år."}]}',
        "tripletex_post",
        {"path": "/salary/transaction"},
    )
    if not _sn or "2026" not in _sn:
        _fail(f"salary 422 tool note (Ugyldig år): expected 2026 hint, got {_sn!r}")
        err += 1
    else:
        _ok("salary 422 _toolNote: Ugyldig år (local)")

    class _ApiNoDelete:
        supplier_invoice_500_seen: set[str] = set()

        def delete(self, *_a, **_k) -> dict:
            raise AssertionError("DELETE employment should be skipped")

    try:
        _del_out = execute_tool(
            "tripletex_delete",
            {"path": "/employee/employment/12345"},
            _ApiNoDelete(),  # type: ignore[arg-type]
        )
        _dd = json.loads(_del_out)
        if not _dd.get("skipped"):
            _fail(f"DELETE employment guard: expected skipped, got {_del_out[:200]!r}")
            err += 1
        else:
            _ok("tripletex_delete /employee/employment/{id} — no HTTP (local)")
    except Exception as e:
        _fail(f"DELETE employment guard: {e}")
        err += 1

    for label, params in (
        ("empty string", {"email": ""}),
        ("whitespace only", {"email": "  \t  "}),
    ):
        try:
            out = execute_tool(
                "tripletex_get",
                {"path": "/employee", "params": params},
                dead,  # type: ignore[arg-type]
            )
            data = json.loads(out)
            msg = str(data.get("error") or "")
            if data.get("http_error") or not msg or "empty" not in msg.lower():
                _fail(f"employee empty-email guard ({label}): expected JSON error about empty email, got {out[:280]!r}")
                err += 1
            else:
                _ok(f"execute_tool GET /employee rejects {label!r} email (no HTTP)")
        except AssertionError as e:
            _fail(f"employee empty-email guard ({label}): {e}")
            err += 1
        except Exception as e:
            _fail(f"employee empty-email guard ({label}): {e}")
            err += 1

    class _ApiNoPostEmployee:
        supplier_invoice_500_seen: set[str] = set()

        def get(self, *_a, **_k) -> dict:
            raise AssertionError("GET unexpected in POST /employee guard test")

        def post(self, *_a, **_k) -> dict:
            raise AssertionError("POST /employee guard should not call api.post")

    try:
        out_pe = execute_tool(
            "tripletex_post",
            {
                "path": "/employee",
                "body": {
                    "firstName": "X",
                    "lastName": "Y",
                    "userType": "STANDARD",
                    "department": {"id": 1},
                    "dateOfBirth": "1990-01-01",
                },
            },
            _ApiNoPostEmployee(),  # type: ignore[arg-type]
        )
        dpe = json.loads(out_pe)
        if not isinstance(dpe.get("error"), str) or "email" not in dpe["error"].lower():
            _fail(f"POST /employee without email: expected error mentioning email, got {out_pe[:320]!r}")
            err += 1
        else:
            _ok("execute_tool POST /employee without email — no HTTP (local)")
    except Exception as e:
        _fail(f"POST /employee email guard: {e}")
        err += 1

    pdf = FileAttachment(filename="x.pdf", content_base64="e30=", mime_type="application/pdf")
    if _pdf_employee_context_hint([], "Registrer ny ansatt") is not None:
        _fail("PDF hint: expected None without PDF attachment")
        err += 1
    else:
        _ok("PDF hint: no attachment → None")

    if _pdf_employee_context_hint([pdf], "Oppdater kundeadresse") is not None:
        _fail("PDF hint: expected None when prompt has no HR keywords")
        err += 1
    else:
        _ok("PDF hint: PDF + non-HR prompt → None")

    h_no = _pdf_employee_context_hint([pdf], "Integrer le nouvel employé selon le contrat")
    if not h_no or "PDF" not in h_no or "e-post" not in h_no:
        _fail(f"PDF hint: expected NO/FR HR prompt to produce hint, got {h_no!r}")
        err += 1
    else:
        _ok("PDF hint: French HR keywords → hint text")

    h_fr = _pdf_employee_context_hint([pdf], "Lettre d'embauche — salaire et date de début")
    if not h_fr or "avdeling" not in h_fr.lower():
        _fail(f"PDF hint: expected French embauche/salaire prompt to produce hint, got {h_fr!r}")
        err += 1
    else:
        _ok("PDF hint: embauche/salaire → hint text")

    h_intacc = _pdf_employee_context_hint(
        [pdf],
        "Vous avez reçu une lettre d'offre (voir PDF). Effectuez l'intégration complète.",
    )
    if not h_intacc or "e-post" not in h_intacc:
        _fail(f"PDF hint: expected FR intégration/lettre/offre prompt → hint, got {h_intacc!r}")
        err += 1
    else:
        _ok("PDF hint: FR lettre d'offre + intégration (accented) → hint text")

    class _PostSupplierInv500:
        def __init__(self) -> None:
            self.supplier_invoice_500_seen: set[str] = set()

        def post(self, *_a, **_k) -> None:
            r = requests.Response()
            r.status_code = 500
            r._content = b'{"status":500,"code":1000,"message":null}'
            r.encoding = "utf-8"
            raise requests.HTTPError(response=r)

    try:
        out = execute_tool(
            "tripletex_post",
            {"path": "/supplierInvoice", "body": {"invoiceNumber": "x", "supplier": {"id": 1}}},
            _PostSupplierInv500(),  # type: ignore[arg-type]
        )
        data = json.loads(out)
        note = str(data.get("_toolNote") or "")
        if data.get("http_error") != 500 or "tripletex_post_voucher" not in note:
            _fail(f"supplierInvoice 500/1000: expected http_error 500 + _toolNote, got {out[:450]!r}")
            err += 1
        else:
            _ok("execute_tool POST /supplierInvoice HTTP 500 code 1000 → _toolNote fallback")
    except Exception as e:
        _fail(f"supplierInvoice 500/1000 test: {e}")
        err += 1

    try:
        post_calls: list[int] = []

        class _Post500Twice:
            def __init__(self) -> None:
                self.supplier_invoice_500_seen: set[str] = set()

            def post(self, *_a, **_k) -> None:
                post_calls.append(1)
                r = requests.Response()
                r.status_code = 500
                r._content = b'{"status":500,"code":1000}'
                r.encoding = "utf-8"
                raise requests.HTTPError(response=r)

        api2 = _Post500Twice()
        body2 = {"invoiceNumber": "INV-DUP", "supplier": {"id": 42}}
        o1 = execute_tool("tripletex_post", {"path": "/supplierInvoice", "body": body2}, api2)  # type: ignore[arg-type]
        o2 = execute_tool("tripletex_post", {"path": "/supplierInvoice", "body": body2}, api2)  # type: ignore[arg-type]
        d2 = json.loads(o2)
        if len(post_calls) != 1:
            _fail(f"supplierInvoice duplicate guard: expected 1 HTTP POST, got {len(post_calls)}")
            err += 1
        elif not d2.get("skipped"):
            _fail(f"supplierInvoice duplicate guard: expected skipped second call, got {o2[:200]!r}")
            err += 1
        else:
            _ok("execute_tool blocks 2nd POST /supplierInvoice after 500/1000 (same invoice+supplier)")
    except Exception as e:
        _fail(f"supplierInvoice duplicate guard: {e}")
        err += 1

    h_pt = _pdf_supplier_invoice_context_hint(
        [pdf],
        "Voce recebeu uma fatura de fornecedor (ver PDF anexo). Registe a fatura.",
    )
    if not h_pt or "tripletex_post_voucher" not in h_pt:
        _fail(f"PDF supplier hint PT: expected voucher mention, got {h_pt!r}")
        err += 1
    else:
        _ok("PDF supplier hint: Portuguese fornecedor+fatura → text")

    h_fr_sup = _pdf_supplier_invoice_context_hint(
        [pdf],
        "Vous avez recu une facture fournisseur (voir PDF). Enregistrez la facture. Creez le fournisseur.",
    )
    if not h_fr_sup or "tripletex_post_voucher" not in h_fr_sup:
        _fail(f"PDF supplier hint FR: expected voucher mention, got {h_fr_sup!r}")
        err += 1
    else:
        _ok("PDF supplier hint: French facture+fournisseur+enregistrer → text")

    class _PostForbidden:
        def post(self, *_a, **_k) -> None:
            raise AssertionError("HTTP post should not run for this guard test")

    try:
        o_guard_act = execute_tool(
            "tripletex_post",
            {"path": "/activity", "body": {"name": "X", "description": "Y"}},
            _PostForbidden(),  # type: ignore[arg-type]
        )
        dga = json.loads(o_guard_act)
        if not isinstance(dga.get("error"), str) or "activitytype" not in dga["error"].lower():
            _fail(f"POST /activity guard: expected error about activityType, got {o_guard_act[:300]!r}")
            err += 1
        else:
            _ok("execute_tool POST /activity without activityType — no HTTP (local)")
    except Exception as e:
        _fail(f"POST /activity guard: {e}")
        err += 1

    class _PostCaptureActivity:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def post(self, path: Any, body: Any, params: Any = None) -> dict[str, Any]:
            self.calls.append((path, body))
            return {"value": {"id": 999}}

    try:
        cap_act = _PostCaptureActivity()
        o_at_str = execute_tool(
            "tripletex_post",
            {
                "path": "/activity",
                "body": {"name": "X", "activityType": "PROJECT_GENERAL_ACTIVITY"},
            },
            cap_act,  # type: ignore[arg-type]
        )
        d_at = json.loads(o_at_str)
        if len(cap_act.calls) != 1:
            _fail(
                f"POST /activity string activityType: expected 1 post, got {len(cap_act.calls)}"
            )
            err += 1
        elif d_at.get("value", {}).get("id") != 999:
            _fail(f"POST /activity string activityType: bad response {o_at_str[:250]!r}")
            err += 1
        else:
            _ok("execute_tool POST /activity with string activityType passes guard (local)")
    except Exception as e:
        _fail(f"POST /activity string activityType: {e}")
        err += 1

    try:
        o_guard_pr = execute_tool(
            "tripletex_post",
            {
                "path": "/project",
                "body": {
                    "name": "P",
                    "customer": {"id": 1},
                    "startDate": "2026-03-01",
                },
            },
            _PostForbidden(),  # type: ignore[arg-type]
        )
        dgp = json.loads(o_guard_pr)
        if not isinstance(dgp.get("error"), str) or "projectmanager" not in dgp["error"].lower():
            _fail(f"POST /project guard: expected error about projectManager, got {o_guard_pr[:300]!r}")
            err += 1
        else:
            _ok("execute_tool POST /project without projectManager — no HTTP (local)")
    except Exception as e:
        _fail(f"POST /project guard: {e}")
        err += 1

    try:
        o_guard_ts = execute_tool(
            "tripletex_post",
            {
                "path": "/timesheet/entry",
                "body": {
                    "project": {"id": 1},
                    "activity": {"id": 1},
                    "employee": {"id": 1},
                    "date": "2026-03-01",
                    "hours": 33,
                },
            },
            _PostForbidden(),  # type: ignore[arg-type]
        )
        dts = json.loads(o_guard_ts)
        if not isinstance(dts.get("error"), str) or "24" not in dts["error"]:
            _fail(
                f"POST /timesheet/entry hours guard: expected error about ≤24, got {o_guard_ts[:300]!r}"
            )
            err += 1
        else:
            _ok("execute_tool POST /timesheet/entry hours > 24 — no HTTP (local)")
    except Exception as e:
        _fail(f"POST /timesheet/entry hours guard: {e}")
        err += 1

    try:
        sp, notes = _apply_tripletex_get_sanitizers(
            "/invoice/999001",
            {"fields": "id,amountIncludingVat,amountOutstanding,dueDate"},
        )
        fs = sp.get("fields", "")
        if "amountIncludingVat" in fs or "dueDate" in fs:
            _fail(
                f"invoice detail fields sanitizer: expected stripped/mapped, got fields={fs!r}"
            )
            err += 1
        elif "invoiceDueDate" not in fs and "amountOutstanding" not in fs:
            _fail(f"invoice detail fields sanitizer: lost valid tokens, fields={fs!r}")
            err += 1
        elif not any("invoice/{id}" in n or "detail" in n for n in notes):
            _fail(f"invoice detail fields sanitizer: expected note, got {notes!r}")
            err += 1
        else:
            _ok("GET /invoice/{id} fields: strips amountIncludingVat; dueDate→invoiceDueDate (local)")
    except Exception as e:
        _fail(f"invoice detail fields sanitizer: {e}")
        err += 1

    try:
        sp, notes = _apply_tripletex_get_sanitizers(
            "/salary/type",
            {"from": 0, "count": 5, "fields": "id,name,displayName"},
        )
        fs = sp.get("fields", "")
        if "displayName" in fs:
            _fail(f"salary/type fields sanitizer: expected displayName stripped, got {fs!r}")
            err += 1
        elif "id" not in fs or "name" not in fs:
            _fail(f"salary/type fields sanitizer: expected id+name, got {fs!r}")
            err += 1
        elif not notes or "salary/type" not in notes[0]:
            _fail(f"salary/type sanitizer: expected note, got {notes!r}")
            err += 1
        else:
            _ok("GET /salary/type fields: strips displayName (local)")
    except Exception as e:
        _fail(f"salary/type fields sanitizer: {e}")
        err += 1

    try:
        sp, notes = _apply_tripletex_get_sanitizers(
            "/activity",
            {"from": 0, "count": 50, "fields": "id,name,activityNumber,isInactive"},
        )
        fs = sp.get("fields", "")
        if "isInactive" in fs or "activityNumber" in fs:
            _fail(f"activity fields sanitizer: expected isInactive+activityNumber stripped, got {fs!r}")
            err += 1
        elif "id" not in fs or "name" not in fs:
            _fail(f"activity fields sanitizer: expected id+name kept, got {fs!r}")
            err += 1
        elif not notes or ("isInactive" not in notes[0] and "activityNumber" not in notes[0]):
            _fail(f"activity sanitizer: expected note mentioning strip, got {notes!r}")
            err += 1
        else:
            _ok("GET /activity fields: strips isInactive + activityNumber (local)")
        sp2, n2 = _apply_tripletex_get_sanitizers(
            "/activity/12",
            {"fields": "id,name,activityNumber,isInactive"},
        )
        fs2 = sp2.get("fields") or ""
        if "isInactive" in fs2 or "activityNumber" in fs2:
            _fail(f"activity/{id} fields sanitizer: expected banned fields stripped, got {fs2!r}")
            err += 1
        elif not n2:
            _fail(f"activity detail sanitizer: expected note, got {n2!r}")
            err += 1
        else:
            _ok("GET /activity/{id} fields: strips isInactive + activityNumber (local)")
    except Exception as e:
        _fail(f"activity fields sanitizer: {e}")
        err += 1

    try:
        sp_v, notes_v = _apply_tripletex_get_sanitizers(
            "/ledger/voucher",
            {"dateFrom": "2026-03-01", "dateTo": "2026-03-32", "fields": "id,date"},
        )
        if sp_v.get("dateTo") != "2026-03-31":
            _fail(
                f"ledger/voucher date clamp: expected dateTo 2026-03-31, got {sp_v.get('dateTo')!r}"
            )
            err += 1
        elif not notes_v or "clamped" not in notes_v[0].lower():
            _fail(f"ledger/voucher date clamp: expected note, got {notes_v!r}")
            err += 1
        else:
            _ok("GET /ledger/voucher: clamps invalid dateTo (e.g. März 32 → 31) (local)")
    except Exception as e:
        _fail(f"ledger/voucher date sanitizer: {e}")
        err += 1

    try:
        lines = [
            {"row": 1, "account": {"id": 1}, "amountGross": 1000},
            {"row": 2, "account": {"id": 2}, "amountGross": -1000, "supplier": {"id": 99}},
        ]
        if _supplier_party_id_from_postings(lines) != 99:
            _fail(
                f"supplier party id: expected 99 from negative line, got {_supplier_party_id_from_postings(lines)!r}"
            )
            err += 1
        else:
            _ok("supplier party id: prefers supplier on credit (negative amount) line (local)")
        lines2 = [
            {"amountGross": 500, "supplier": {"id": 7}},
        ]
        if _supplier_party_id_from_postings(lines2) != 7:
            _fail(f"supplier party id: expected 7, got {_supplier_party_id_from_postings(lines2)!r}")
            err += 1
        else:
            _ok("supplier party id: falls back to any line with supplier (local)")
        lines3 = list(lines)
        _merge_customer_into_positive_posting_lines(lines3, {"id": 99})
        if lines3[0].get("customer") != {"id": 99} or lines3[1].get("customer") is not None:
            _fail(f"merge customer on debits: unexpected {lines3!r}")
            err += 1
        else:
            _ok("merge customer: only positive lines without customer (local)")
    except Exception as e:
        _fail(f"supplier voucher party merge helpers: {e}")
        err += 1

    try:
        if not _tripletex_get_path_is_session_cacheable("/ledger/account"):
            _fail("GET cache allowlist: /ledger/account should be cacheable")
            err += 1
        elif _tripletex_get_path_is_session_cacheable("/ledger/voucher"):
            _fail("GET cache denylist: /ledger/voucher must not be cached")
            err += 1
        elif _tripletex_get_path_is_session_cacheable("/invoice/1"):
            _fail("GET cache denylist: /invoice/* must not be cached")
            err += 1
        elif _get_params_cache_key({"b": 1, "a": 2}) != _get_params_cache_key({"a": 2, "b": 1}):
            _fail("GET cache key: params should be order-independent")
            err += 1
        else:
            _ok("GET session-cache allowlist + stable param key (local)")
    except Exception as e:
        _fail(f"GET cache helpers: {e}")
        err += 1

    try:
        if not TripletexAPI._ledger_account_mutation_path("/ledger/account"):
            _fail("ledger account mutation path: /ledger/account should match")
            err += 1
        elif not TripletexAPI._ledger_account_mutation_path("/ledger/account/99"):
            _fail("ledger account mutation path: /ledger/account/99 should match")
            err += 1
        elif TripletexAPI._ledger_account_mutation_path("/ledger/accountingDimensionName"):
            _fail("ledger account mutation path: accountingDimension must not match")
            err += 1
        elif TripletexAPI._put_customer_dedupe_key("/customer/5", {"a": 1}) is None:
            _fail("customer PUT dedupe key: expected key for /customer/5")
            err += 1
        elif TripletexAPI._put_customer_dedupe_key("/customer/x", {}) is not None:
            _fail("customer PUT dedupe key: non-numeric id should not dedupe")
            err += 1
        else:
            _ok("ledger account mutation path + customer PUT dedupe key (local)")
    except Exception as e:
        _fail(f"mutation/dedupe helpers: {e}")
        err += 1

    try:
        q = _ledger_account_3400_fee_credit_quirk_note(
            "3400", {"id": 1, "name": "Spesielt offentlig tilskudd"}
        )
        if not q or "tilskudd" not in q.lower():
            _fail(f"3400 quirk note: expected tilskudd hint, got {q!r}")
            err += 1
        elif _ledger_account_3400_fee_credit_quirk_note("3400", {"name": "Purregebyrinntekt"}) is not None:
            _fail("3400 quirk note: should be None for fee-like name")
            err += 1
        elif _ledger_account_3400_fee_credit_quirk_note("6010", {"name": "tilskudd"}) is not None:
            _fail("3400 quirk note: should only fire for account 3400")
            err += 1
        else:
            _ok("ledger account 3400 / purregebyr name quirk note (local)")
    except Exception as e:
        _fail(f"3400 quirk helper: {e}")
        err += 1

    try:
        minimal = {"employee": {"id": 42}, "startDate": "2026-12-01"}
        seq = _employment_post_attempt_sequence(minimal)
        _n = len(_employment_division_post_ids())
        if len(seq) != _n + 1:
            _fail(
                f"employment sequence: expected {_n + 1} bodies ({_n} division tries + minimal), got {len(seq)}"
            )
            err += 1
        else:
            bad = False
            for i, div_id in enumerate(_employment_division_post_ids()):
                if seq[i].get("division") != {"id": div_id}:
                    bad = True
                    break
            if bad or seq[_n] != minimal:
                _fail(f"employment sequence: unexpected bodies {seq!r}")
                err += 1
            else:
                _ok(f"employment POST sequence: division 1..{_n} then minimal (local)")
        seq_one = _employment_post_attempt_sequence(
            {"employee": {"id": 1}, "startDate": "2026-01-01", "division": {"id": 9}}
        )
        if len(seq_one) != 1:
            _fail(f"employment sequence w/ division: expected 1 body, got {len(seq_one)}")
            err += 1
        else:
            _ok("employment POST sequence: body with division → single attempt (local)")
        seq_url = _employment_post_attempt_sequence(
            {
                "employee": {"id": 7, "url": "https://example.invalid/employee/7"},
                "startDate": "2026-02-01",
            }
        )
        if len(seq_url) != _n + 1:
            _fail(
                f"employment sequence w/ employee.url: expected {_n + 1} bodies, got {len(seq_url)}"
            )
            err += 1
        elif seq_url[0].get("employee") != {"id": 7} or seq_url[0].get("division") != {"id": 1}:
            _fail(f"employment sequence w/ employee.url: bad first body {seq_url[0]!r}")
            err += 1
        elif seq_url[-1] != {"employee": {"id": 7}, "startDate": "2026-02-01"}:
            _fail(f"employment sequence w/ employee.url: bad minimal tail {seq_url[-1]!r}")
            err += 1
        else:
            _ok("employment POST sequence: employee {id,url} → normalised division sweep (local)")
    except Exception as e:
        _fail(f"employment sequence test: {e}")
        err += 1

    try:

        class _PutShouldNotRun:
            def put(self, path: str, body: dict) -> dict:
                raise RuntimeError("PUT should be skipped when division 1+2 already 422")

        _reset_per_solve_guards()
        _eid_skip = 9_001_002
        _employment_division_put_rejected[_eid_skip] = {1, 2}
        _out_skip = execute_tool(
            "tripletex_put",
            {"path": f"/employee/employment/{_eid_skip}", "body": {"division": {"id": 3}}},
            _PutShouldNotRun(),  # type: ignore[arg-type]
        )
        _data_skip = json.loads(_out_skip)
        if not _data_skip.get("skipped"):
            _fail(f"employment PUT division skip: expected skipped, got {_data_skip!r}")
            err += 1
        elif _data_skip.get("divisionIdSkipped") != 3:
            _fail(f"employment PUT division skip: expected divisionIdSkipped 3, got {_data_skip!r}")
            err += 1
        else:
            _ok("employment PUT division: skip HTTP for id 3 when 1+2 rejected (local)")
        _reset_per_solve_guards()
    except Exception as e:
        _fail(f"employment PUT division skip test: {e}")
        err += 1

    try:

        class _EmpSweepMinimalApi:
            """404 on POST with division; 200 on minimal — mirrors tenants where PUT division always 422."""

            def __init__(self) -> None:
                self.employment_post_minimal_fallback_ids: set[int] = set()

            def post(self, path: str, body: Any, params: Any = None) -> dict[str, Any]:
                if "/employee/employment" in (path or "") and "/details" not in (path or ""):
                    if isinstance(body, dict) and body.get("division"):
                        e = requests.HTTPError()
                        e.response = type("R", (), {"status_code": 404, "text": ""})()
                        raise e
                    return {"value": {"id": 884_402}}
                raise AssertionError(f"unexpected post {path!r}")

            def put(self, path: str, body: dict) -> dict:
                raise RuntimeError("PUT should be skipped for minimal-fallback employment")

        _es = _EmpSweepMinimalApi()
        _sweep_post = execute_tool(
            "tripletex_post",
            {
                "path": "/employee/employment",
                "body": {"employee": {"id": 1}, "startDate": "2026-05-01"},
            },
            _es,  # type: ignore[arg-type]
        )
        _d_sp = json.loads(_sweep_post)
        if _d_sp.get("value", {}).get("id") != 884_402:
            _fail(f"employment sweep POST: bad value {_sweep_post[:200]!r}")
            err += 1
        elif 884_402 not in _es.employment_post_minimal_fallback_ids:
            _fail("employment sweep POST: expected employment id in employment_post_minimal_fallback_ids")
            err += 1
        else:
            _out_mf = execute_tool(
                "tripletex_put",
                {"path": "/employee/employment/884402", "body": {"division": {"id": 1}}},
                _es,  # type: ignore[arg-type]
            )
            _d_mf = json.loads(_out_mf)
            if not _d_mf.get("skipped") or _d_mf.get("minimalFallbackDivisionPutSkipped") != 1:
                _fail(f"employment PUT division minimal-fallback skip: expected skipped+flag, got {_out_mf[:350]!r}")
                err += 1
            else:
                _ok("employment PUT division: skip when POST was minimal-after-division-sweep (local)")
    except Exception as e:
        _fail(f"employment minimal-fallback PUT skip test: {e}")
        err += 1

    def _env_pop(key: str) -> Optional[str]:
        return os.environ.pop(key, None)

    def _env_set(key: str, val: Optional[str]) -> None:
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val

    class _PayInvMock:
        def __init__(self, outstanding: float):
            self._o = outstanding

        def get(self, path: str, params: Optional[dict] = None) -> dict:
            assert "/invoice/" in path
            return {
                "value": {
                    "amountOutstanding": self._o,
                    "amountCurrencyOutstanding": self._o,
                }
            }

    prev_pay_act = os.environ.get("TRIPLETEX_PAYMENT_PAIDAMOUNT_ACTION")
    try:
        os.environ["TRIPLETEX_PAYMENT_PAIDAMOUNT_ACTION"] = "clamp"
        prms = {"paidAmount": 19096.0, "paymentTypeId": 1}
        pm = _PayInvMock(2238.75)
        blocked = _apply_invoice_payment_paid_amount_guard(pm, "/invoice/2147/:payment", prms)
        if blocked is not None:
            _fail(f"pay guard clamp: expected None, got {blocked!r}")
            err += 1
        elif prms.get("paidAmount") != 2238.75:
            _fail(f"pay guard clamp: expected 2238.75, got {prms.get('paidAmount')!r}")
            err += 1
        else:
            _ok("invoice :payment guard clamps FCY×rate-style paidAmount (local)")

        os.environ["TRIPLETEX_PAYMENT_PAIDAMOUNT_ACTION"] = "block"
        prms2 = {"paidAmount": 19096.0, "paymentTypeId": 1}
        bl = _apply_invoice_payment_paid_amount_guard(pm, "/invoice/2147/:payment", prms2)
        if not isinstance(bl, dict) or bl.get("error") != "invoice_payment_paid_amount_guard":
            _fail(f"pay guard block: expected structured error, got {bl!r}")
            err += 1
        else:
            _ok("invoice :payment guard block mode (local)")
    except Exception as e:
        _fail(f"payment paidAmount guard tests: {e}")
        err += 1
    finally:
        _env_set("TRIPLETEX_PAYMENT_PAIDAMOUNT_ACTION", prev_pay_act)

    prev_tid = _env_pop("TASK_ID")
    prev_nmt = _env_pop("NM_TASK_ID")
    prev_def_tid = _env_pop("TRIPLETEX_DEFAULT_TASK_ID")
    try:
        req = SolveRequest(
            prompt="x",
            files=[],
            tripletex_credentials=TripletexCredentials(
                base_url="https://example.invalid/v2",
                session_token="x",
            ),
        )
        lab = _resolve_task_label(req, None)
        if lab.startswith("(not set"):
            _ok("task label placeholder when no task_id / env")
        else:
            _fail(f"expected placeholder task label, got {lab!r}")
            err += 1
        os.environ["TRIPLETEX_DEFAULT_TASK_ID"] = "11"
        lab2 = _resolve_task_label(req, None)
        if lab2 != "11":
            _fail(f"TRIPLETEX_DEFAULT_TASK_ID: expected '11', got {lab2!r}")
            err += 1
        else:
            _ok("TRIPLETEX_DEFAULT_TASK_ID labels logs when task_id unset (local)")
    except Exception as e:
        _fail(f"task label default env: {e}")
        err += 1
    finally:
        _env_set("TRIPLETEX_DEFAULT_TASK_ID", prev_def_tid)
        _env_set("TASK_ID", prev_tid)
        _env_set("NM_TASK_ID", prev_nmt)

    class _BankGLMock:
        def get(self, path: str, params: Optional[dict] = None) -> dict:
            if "/ledger/account/5001" in path.replace("//", "/"):
                return {
                    "value": {
                        "id": 5001,
                        "number": 1920,
                        "isBankAccount": True,
                        "name": "Bankinnskudd",
                    }
                }
            return {"value": {"id": 5002, "number": 6800, "isBankAccount": False, "name": "Kostnad"}}

    prev_allow_bank = os.environ.get("TRIPLETEX_VOUCHER_ALLOW_BANK_LINES")
    try:
        _env_set("TRIPLETEX_VOUCHER_ALLOW_BANK_LINES", None)
        be = _reject_manual_voucher_bank_lines(
            _BankGLMock(),
            [
                {"account": {"id": 5001}, "amountGross": 100},
                {"account": {"id": 5002}, "amountGross": -100},
            ],
        )
        if not be or be.get("error") != "manual_voucher_bank_account":
            _fail(f"bank voucher guard: expected manual_voucher_bank_account, got {be!r}")
            err += 1
        else:
            _ok("tripletex_post_voucher guard rejects isBankAccount line (local)")
        os.environ["TRIPLETEX_VOUCHER_ALLOW_BANK_LINES"] = "1"
        be2 = _reject_manual_voucher_bank_lines(
            _BankGLMock(),
            [{"account": {"id": 5001}, "amountGross": 100}],
        )
        if be2 is not None:
            _fail(f"TRIPLETEX_VOUCHER_ALLOW_BANK_LINES=1 should skip guard, got {be2!r}")
            err += 1
        else:
            _ok("TRIPLETEX_VOUCHER_ALLOW_BANK_LINES=1 skips bank-line guard (local)")
    except Exception as e:
        _fail(f"bank voucher guard tests: {e}")
        err += 1
    finally:
        _env_set("TRIPLETEX_VOUCHER_ALLOW_BANK_LINES", prev_allow_bank)

    try:
        n = _normalize_voucher_posting_line(
            {"amountGross": 100, "accountingDimensionValues": [{"id": 42}]}
        )
        if n.get("freeAccountingDimension1") != {"id": 42} or n.get("accountingDimensionValues") is not None:
            _fail(f"voucher dim map: expected freeAccountingDimension1 {{id:42}}, got {n!r}")
            err += 1
        else:
            _ok("voucher posting: accountingDimensionValues → freeAccountingDimension1 (local)")
        n2 = _normalize_voucher_posting_line(
            {
                "amountGross": 1,
                "freeAccountingDimension1": {"id": 7},
                "accountingDimensionValues": [{"id": 99}],
            }
        )
        if n2.get("freeAccountingDimension1") != {"id": 7} or n2.get("freeAccountingDimension2") != {"id": 99}:
            _fail(f"voucher dim merge order: expected slot1=7 slot2=99, got {n2!r}")
            err += 1
        else:
            _ok("voucher posting: free slots then accountingDimensionValues (local)")
    except Exception as e:
        _fail(f"voucher dimension normalizer: {e}")
        err += 1

    return err


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

    try:
        st = api.get(
            "/salary/type",
            params={"from": 0, "count": 3, "fields": "id,name,displayName"},
        )
        vals = st.get("values") if isinstance(st, dict) else None
        if not isinstance(vals, list) or not vals:
            _fail(f"GET /salary/type after sanitize: unexpected {json.dumps(st, ensure_ascii=False)[:200]}")
            errors += 1
        else:
            _ok("GET /salary/type with displayName in fields — sanitizer avoids 400")
    except Exception as e:
        _fail(f"GET /salary/type: {e}")
        errors += 1

    return errors


def run_supplier_invoice_check(api: TripletexAPI) -> int:
    """
    Create a disposable supplier (unique org.nr) and POST /supplierInvoice.
    Tries several bodies (amount + due date, orderLines, etc.); prints **full** error
    bodies to stderr on failure. If every attempt is HTTP 500 code 1000, WARN+skip (NM sandbox).
    """
    suffix = str(int(time.time()))[-8:]
    org = ("9" + suffix.zfill(8))[:9]

    body_cust = {
        "name": "Sandbox test leverandør",
        "isSupplier": True,
        "isCustomer": False,
        "organizationNumber": org,
    }
    try:
        cust_res = api.post("/customer", body_cust)
    except requests.HTTPError as e:
        st = getattr(e.response, "status_code", "?")
        tx = (e.response.text or "")[:400] if e.response else ""
        _fail(f"POST /customer HTTP {st}: {tx}")
        return 1

    val = cust_res.get("value") if isinstance(cust_res, dict) else None
    sid = val.get("id") if isinstance(val, dict) else None
    if sid is None:
        _fail(f"POST /customer: missing value.id in {json.dumps(cust_res, ensure_ascii=False)[:280]}")
        return 1
    _ok(f"POST /customer → supplier id={sid} org={org}")

    try:
        snap = api.get(f"/customer/{sid}", params={"fields": "id,isCustomer,isSupplier"})
        iv = snap.get("value") if isinstance(snap, dict) else None
        if isinstance(iv, dict) and iv.get("isCustomer") is True:
            api.put(f"/customer/{sid}", {"isCustomer": False})
            _ok(f"PUT /customer/{sid} {{isCustomer:false}} (tenant quirk)")
    except Exception as e:
        print(f"  WARN  isCustomer check skipped: {e}", file=sys.stderr)

    inv_no = f"SBX-{suffix}-{uuid.uuid4().hex[:8]}"
    inv_date = date.today().isoformat()
    due14 = (date.today() + timedelta(days=14)).isoformat()
    due30 = (date.today() + timedelta(days=30)).isoformat()
    base: dict = {
        "invoiceNumber": inv_no,
        "invoiceDate": inv_date,
        "supplier": {"id": sid},
        "amountCurrency": 1000,
        "currency": {"id": 1},
    }
    attempts: list[tuple[str, dict]] = [
        ("amountCurrency+dueDate+14d", {**base, "invoiceDueDate": due14}),
        ("amountCurrency+dueDate+30d", {**base, "invoiceDueDate": due30}),
        (
            "amountNOK+dueDate+14d",
            {
                **{k: v for k, v in base.items() if k != "amountCurrency"},
                "amount": 1000,
                "invoiceDueDate": due14,
            },
        ),
        (
            "orderLines+amountCurrency+due14d",
            {
                **base,
                "invoiceDueDate": due14,
                "orderLines": [
                    {
                        "description": "Sandbox order line",
                        "count": 1,
                        "unitPriceExcludingVatCurrency": 800,
                        "vatType": {"id": 1},
                    }
                ],
            },
        ),
        (
            "orderLines_only+due14d",
            {
                "invoiceNumber": inv_no + "-OL",
                "invoiceDate": inv_date,
                "invoiceDueDate": due14,
                "supplier": {"id": sid},
                "currency": {"id": 1},
                "orderLines": [
                    {
                        "description": "Sandbox line only",
                        "count": 1,
                        "unitPriceExcludingVatCurrency": 800,
                        "vatType": {"id": 1},
                    }
                ],
            },
        ),
        (
            "kidOrReceiverReference+due14d",
            {**base, "invoiceDueDate": due14, "kidOrReceiverReference": "1234567890123"},
        ),
    ]

    inv_res: Optional[dict] = None
    mode = ""
    err_parts: list[str] = []
    for tag, body in attempts:
        try:
            inv_res = api.post("/supplierInvoice", body)
            mode = tag
            break
        except requests.HTTPError as e:
            resp = e.response
            if resp is not None:
                st = getattr(resp, "status_code", "?")
                raw = (resp.text or "").strip()
                print(
                    f"  DIAG  POST /supplierInvoice [{tag}] HTTP {st} FULL body:\n{raw or '(empty)'}",
                    file=sys.stderr,
                    flush=True,
                )
                err_parts.append(f"{tag} HTTP {st}: {raw[:400] or '(empty body)'}")
            else:
                err_parts.append(f"{tag} HTTPError without response: {e!r}")
        except requests.RequestException as e:
            err_parts.append(f"{tag} {type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            err_parts.append(f"{tag} {type(e).__name__}: {e}")

    if inv_res is None:
        # NM sandkasse returnerer ofte HTTP 500 **code 1000** uten `validationMessages` på POST /supplierInvoice
        # selv med beløp + forfallsdato — da er proben ikke et nyttig signal; ikke feil hele suiten.
        def _each_500_code_1000(parts: list[str]) -> bool:
            if not parts:
                return False
            for p in parts:
                if "HTTP 500" not in p:
                    return False
                if not any(s in p for s in ('"code":1000', '"code": 1000')):
                    return False
            return True

        if _each_500_code_1000(err_parts):
            print(
                "  WARN  POST /supplierInvoice: all bodies got HTTP 500 code 1000 on this tenant — "
                "skipping probe (known sandbox quirk; agent flow still documented in SYSTEM_PROMPT).",
                file=sys.stderr,
                flush=True,
            )
            return 0
        _fail("POST /supplierInvoice all attempts failed — " + " | ".join(err_parts))
        return 1

    if not isinstance(inv_res, dict):
        _fail("POST /supplierInvoice: unexpected response type")
        return 1
    v = inv_res.get("value")
    vid = v.get("id") if isinstance(v, dict) else None
    _ok(f"POST /supplierInvoice ({mode}) → invoice id={vid} number={inv_no}")
    return 0


def _ledger_account_id(api: TripletexAPI, number: str) -> Optional[int]:
    try:
        r = api.get("/ledger/account", params={"number": number, "fields": "id,number"})
    except requests.HTTPError:
        return None
    vals = r.get("values") if isinstance(r, dict) else None
    if isinstance(vals, list) and vals and isinstance(vals[0], dict):
        raw = vals[0].get("id")
        return int(raw) if isinstance(raw, int) else None
    return None


def _first_supplier_customer_id(api: TripletexAPI) -> Optional[int]:
    """Some sandboxes require **supplier** on leverandørgjeld (24xx) voucher lines."""
    try:
        r = api.get(
            "/customer",
            params={"from": 0, "count": 100, "fields": "id,isSupplier"},
        )
    except requests.HTTPError:
        return None
    for row in r.get("values") or []:
        if not isinstance(row, dict) or row.get("isSupplier") is not True:
            continue
        raw = row.get("id")
        if isinstance(raw, int):
            return raw
    return None


def _run_post_voucher_probe(
    api: TripletexAPI,
    *,
    lines: list[dict],
    description: str,
    ok_label: str,
) -> int:
    """Shared success/fail handling for post_voucher_two_step."""
    try:
        result = post_voucher_two_step(
            api,
            date=date.today().isoformat(),
            description=description,
            postings_lines=lines,
            send_to_ledger=False,
        )
    except requests.HTTPError as e:
        resp = getattr(e, "response", None)
        st = resp.status_code if resp is not None else "?"
        raw = ((resp.text or "").strip()[:500] if resp is not None else "") or str(e) or "(no detail)"
        _fail(f"post_voucher_two_step HTTP {st}: {raw}")
        return 1
    except Exception as e:
        _fail(f"post_voucher_two_step: {e}")
        return 1

    if result.get("error"):
        _fail(
            f"voucher: {result.get('error')} mode={result.get('posting_mode')} "
            f"keys={list(result.keys())}"
        )
        return 1

    vid = result.get("voucherId")
    if vid is None:
        _fail(f"voucher: no voucherId in result: {json.dumps(result, ensure_ascii=False)[:400]}")
        return 1

    for i, item in enumerate(result.get("postingResponses") or []):
        if isinstance(item, dict) and item.get("http_error"):
            _fail(f"voucher postingResponses[{i}]: {json.dumps(item, ensure_ascii=False)[:350]}")
            return 1

    _ok(f"post_voucher_two_step → voucherId={vid} posting_mode={result.get('posting_mode')} — {ok_label}")
    return 0


def run_voucher_probe(api: TripletexAPI) -> int:
    """
    2-line balanced journal (non-bank). Default 6800 / 2900 — **not** 1920 (manual voucher bank lines are blocked).
    Override with TRIPLETEX_VOUCHER_DEBIT_NUMBER / _CREDIT_NUMBER; set TRIPLETEX_VOUCHER_ALLOW_BANK_LINES=1 to test 1920.
    """
    debit_n = os.environ.get("TRIPLETEX_VOUCHER_DEBIT_NUMBER", "6800").strip()
    credit_n = os.environ.get("TRIPLETEX_VOUCHER_CREDIT_NUMBER", "2900").strip()
    id_debit = _ledger_account_id(api, debit_n)
    id_credit = _ledger_account_id(api, credit_n)
    if id_debit is None or id_credit is None:
        _fail(
            f"voucher (simple): need accounts {debit_n} & {credit_n} "
            f"(ids {id_debit}, {id_credit}). Set TRIPLETEX_VOUCHER_DEBIT_NUMBER / _CREDIT_NUMBER."
        )
        return 1

    lines = [
        {"row": 1, "account": {"id": id_debit}, "amountGross": 100},
        {"row": 2, "account": {"id": id_credit}, "amountGross": -100},
    ]
    return _run_post_voucher_probe(
        api,
        lines=lines,
        description="Sandbox voucher probe — simple 2-line (test_sandbox.py)",
        ok_label=f"{debit_n} +100 / {credit_n} −100",
    )


def run_voucher_probe_vat(api: TripletexAPI) -> int:
    """
    3-line pattern like leverandørfaktura-journal: kostnad + inngående MVA + leverandørgjeld.
    Beløp: 800 + 200 − 1000. vatType {{id:1}} på kostnadslinje (som i agent-logger).
    Many tenants require **supplier** on the AP (24xx) line — set **TRIPLETEX_VOUCHER_SUPPLIER_ID**
    or we attach the first **isSupplier** customer from GET /customer.
    """
    exp_n = os.environ.get("TRIPLETEX_VOUCHER_EXPENSE_NUMBER", "7300").strip()
    vat_n = os.environ.get("TRIPLETEX_VOUCHER_VAT_NUMBER", "2710").strip()
    ap_n = os.environ.get("TRIPLETEX_VOUCHER_AP_NUMBER", "2400").strip()

    id_e = _ledger_account_id(api, exp_n)
    id_v = _ledger_account_id(api, vat_n)
    id_ap = _ledger_account_id(api, ap_n)
    if id_e is None or id_v is None or id_ap is None:
        _fail(
            f"voucher (3-line): need {exp_n}, {vat_n}, {ap_n} "
            f"(ids {id_e}, {id_v}, {id_ap}). Set TRIPLETEX_VOUCHER_EXPENSE_NUMBER / _VAT_NUMBER / _AP_NUMBER."
        )
        return 1

    sup_raw = os.environ.get("TRIPLETEX_VOUCHER_SUPPLIER_ID", "").strip()
    sup_id: Optional[int] = int(sup_raw) if sup_raw.isdigit() else _first_supplier_customer_id(api)

    lines: list[dict] = [
        {
            "row": 1,
            "account": {"id": id_e},
            "amountGross": 800,
            "vatType": {"id": 1},
        },
        {"row": 2, "account": {"id": id_v}, "amountGross": 200},
        {"row": 3, "account": {"id": id_ap}, "amountGross": -1000},
    ]
    ok_extra = ""
    if sup_id is not None:
        lines[2]["supplier"] = {"id": sup_id}
        ok_extra = f" supplier={sup_id}"
    else:
        print(
            "  WARN  3-line VAT: no supplier id (set TRIPLETEX_VOUCHER_SUPPLIER_ID or create a supplier); "
            "probe may 422 «Leverandør mangler» on 24xx",
            file=sys.stderr,
            flush=True,
        )

    return _run_post_voucher_probe(
        api,
        lines=lines,
        description="Sandbox voucher probe — 3-line VAT (test_sandbox.py)",
        ok_label=f"{exp_n} +800 (vatType 1) / {vat_n} +200 / {ap_n} −1000{ok_extra}",
    )


def run_dimension_voucher_probe(api: TripletexAPI) -> int:
    """
    Task ~06 smoke: POST dimension name + two values, then balanced journal — debit line carries
    **accountingDimensionValues** (second value), credit on a **different** GL (never same account both sides).
    """
    suffix = uuid.uuid4().hex[:8]
    dim_name = f"SBX_Kost_{suffix}"
    v_mark = f"SBX_M_{suffix}"
    v_kund = f"SBX_K_{suffix}"

    try:
        dn = api.post("/ledger/accountingDimensionName", {"dimensionName": dim_name})
    except requests.HTTPError as e:
        _fail(f"POST accountingDimensionName: {e}")
        return 1
    if not isinstance(dn, dict) or not isinstance(dn.get("value"), dict):
        _fail(f"POST accountingDimensionName: unexpected {json.dumps(dn, ensure_ascii=False)[:200]}")
        return 1

    try:
        r_m = api.post("/ledger/accountingDimensionValue", {"displayName": v_mark})
        r_k = api.post("/ledger/accountingDimensionValue", {"displayName": v_kund})
    except requests.HTTPError as e:
        _fail(f"POST accountingDimensionValue: {e}")
        return 1

    def _val_id(res: Any) -> Optional[int]:
        if not isinstance(res, dict):
            return None
        inner = res.get("value")
        if not isinstance(inner, dict):
            return None
        raw = inner.get("id")
        return int(raw) if isinstance(raw, int) else None

    id_mark = _val_id(r_m)
    id_kund = _val_id(r_k)
    if id_mark is None or id_kund is None:
        _fail(
            f"dimension values: missing id (mark={id_mark!r} kund={id_kund!r}) "
            f"{json.dumps(r_m, ensure_ascii=False)[:120]} …"
        )
        return 1

    debit_n = os.environ.get("TRIPLETEX_DIM_VOUCHER_DEBIT_NUMBER", "6800").strip()
    credit_n = os.environ.get("TRIPLETEX_DIM_VOUCHER_CREDIT_NUMBER", "2900").strip()
    id_debit = _ledger_account_id(api, debit_n)
    id_credit = _ledger_account_id(api, credit_n)
    if id_debit is None or id_credit is None:
        _fail(
            f"dimension voucher: need GL {debit_n} & {credit_n} (ids {id_debit}, {id_credit}). "
            "Set TRIPLETEX_DIM_VOUCHER_DEBIT_NUMBER / _CREDIT_NUMBER."
        )
        return 1
    if id_debit == id_credit:
        _fail("dimension voucher: debit and credit account ids must differ")
        return 1

    amt = 103.0
    lines: list[dict] = [
        {
            "row": 1,
            "account": {"id": id_debit},
            "amountGross": amt,
            "accountingDimensionValues": [{"id": id_kund}],
        },
        {"row": 2, "account": {"id": id_credit}, "amountGross": -amt},
    ]
    return _run_post_voucher_probe(
        api,
        lines=lines,
        description=f"Sandbox dimension+voucher {dim_name} → {v_kund} (test_sandbox.py)",
        ok_label=f"{debit_n} +{amt} (dim value id {id_kund}) / {credit_n} −{amt}",
    )


def _month_bounds(today: date) -> tuple[date, date]:
    first = today.replace(day=1)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1)
    else:
        nxt = first.replace(month=first.month + 1)
    last = nxt - timedelta(days=1)
    return first, last


def run_ledger_probe(api: TripletexAPI) -> int:
    """
    Exercises the ledger period-analysis path: list → voucher detail (amounts + account) → posting GET → POST /project.
    Creates a 2-line journal if the sandbox month list is empty.
    """
    err = 0
    today = date.today()
    m0, m1 = _month_bounds(today)

    def list_month(d0: date, d1: date) -> dict:
        return api.get(
            "/ledger/voucher",
            params={
                "dateFrom": d0.isoformat(),
                "dateTo": d1.isoformat(),
                "from": 0,
                "count": 100,
                "fields": "id,date,description,number",
            },
        )

    try:
        lst = list_month(m0, m1)
        fr = lst.get("fullResultSize")
        n = len(lst.get("values") or [])
        _ok(f"GET /ledger/voucher month {m0}..{m1} fullResultSize={fr} page={n}")
    except Exception as e:
        _fail(f"ledger list month: {e}")
        return 1

    vals = lst.get("values") or []
    # Second month slice (period-compare sanity — same query shape as NM ledger tasks).
    try:
        pm_end = m0 - timedelta(days=1)
        pm_start = pm_end.replace(day=1)
        prev_lst = list_month(pm_start, pm_end)
        _ok(
            f"GET /ledger/voucher prev month {pm_start}..{pm_end} "
            f"fullResultSize={prev_lst.get('fullResultSize')}"
        )
    except Exception as e:
        _fail(f"ledger list previous month: {e}")
        err += 1

    if not vals:
        # 6800/2900 — avoid 1920 on manual vouchers (runtime blocks isBankAccount unless ALLOW_BANK_LINES).
        debit_n = os.environ.get("TRIPLETEX_LEDGER_PROBE_DEBIT_NUMBER", "6800").strip()
        credit_n = os.environ.get("TRIPLETEX_LEDGER_PROBE_CREDIT_NUMBER", "2900").strip()
        id_debit = _ledger_account_id(api, debit_n)
        id_credit = _ledger_account_id(api, credit_n)
        if id_debit is None or id_credit is None:
            _fail(f"ledger-probe: need accounts {debit_n} & {credit_n} to seed a voucher")
            return 1
        try:
            seed = post_voucher_two_step(
                api,
                date=today.isoformat(),
                description="Sandbox --ledger-probe seed journal",
                postings_lines=[
                    {"row": 1, "account": {"id": id_debit}, "amountGross": 25},
                    {"row": 2, "account": {"id": id_credit}, "amountGross": -25},
                ],
                send_to_ledger=True,
            )
        except Exception as e:
            _fail(f"ledger-probe seed voucher: {e}")
            return 1
        if seed.get("error"):
            _fail(f"ledger-probe seed voucher: {seed.get('error')}")
            return 1
        _ok(f"seed post_voucher_two_step → voucherId={seed.get('voucherId')}")
        lst = list_month(m0, m1)
        vals = lst.get("values") or []

    vid = vals[0].get("id") if vals else None
    if vid is None:
        _fail("ledger-probe: no voucher id after list/seed")
        return 1

    # Raw GET without fields — expect posting stubs or minimal shape
    try:
        raw = api.get(f"/ledger/voucher/{vid}", params={})
        posts_raw = (raw.get("value") or {}).get("postings") or []
        stub_only = bool(
            posts_raw
            and all(
                isinstance(p, dict)
                and "amountGross" not in p
                and "account" not in p
                and ("url" in p or len(p) <= 3)
                for p in posts_raw
            )
        )
        _ok(
            f"GET /ledger/voucher/{{id}} (no fields) postings={len(posts_raw)} "
            f"stub_like={stub_only}"
        )
    except Exception as e:
        _fail(f"ledger raw voucher GET: {e}")
        err += 1

    # execute_tool augments fields → amounts + account
    try:
        tool_json = execute_tool(
            "tripletex_get",
            {"path": f"/ledger/voucher/{vid}", "params": {}},
            api,
        )
        detail = json.loads(tool_json)
        posts = (detail.get("value") or {}).get("postings") or []
        ok_lines = 0
        for p in posts:
            if not isinstance(p, dict):
                continue
            acc = p.get("account")
            if p.get("amountGross") is None:
                continue
            if not isinstance(acc, dict) or acc.get("number") is None:
                continue
            ok_lines += 1
        if ok_lines < 1:
            _fail(
                f"execute_tool tripletex_get voucher detail: expected ≥1 posting with amountGross+account.number; "
                f"got {json.dumps(posts, ensure_ascii=False)[:500]}"
            )
            err += 1
        else:
            _ok(f"execute_tool GET /ledger/voucher/{{id}} → {ok_lines} posting(s) with amountGross + account.number")
    except Exception as e:
        _fail(f"execute_tool voucher detail: {e}")
        err += 1

    # GET /ledger/posting/{id}
    pid = None
    try:
        slim = api.get(
            f"/ledger/voucher/{vid}",
            params={"fields": "postings(id)"},
        )
        p0 = ((slim.get("value") or {}).get("postings") or [{}])[0]
        pid = p0.get("id") if isinstance(p0, dict) else None
    except Exception as e:
        _fail(f"ledger voucher postings(id) for posting probe: {e}")
        err += 1

    if pid:
        try:
            pr = api.get(
                f"/ledger/posting/{pid}",
                params={
                    "fields": "id,row,amountGross,amountGrossCurrency,account(id,number,name)",
                },
            )
            pv = pr.get("value") or {}
            if pv.get("amountGross") is None:
                _fail(f"GET /ledger/posting/{pid} missing amountGross: {json.dumps(pv)[:300]}")
                err += 1
            else:
                _ok(f"GET /ledger/posting/{pid} amountGross={pv.get('amountGross')} account={pv.get('account')}")
        except Exception as e:
            _fail(f"GET /ledger/posting/{pid}: {e}")
            err += 1

    # Mini aggregation: sum |amountGross| per account number on this voucher (tool-augmented detail)
    try:
        tool_json = execute_tool(
            "tripletex_get",
            {"path": f"/ledger/voucher/{vid}", "params": {}},
            api,
        )
        detail = json.loads(tool_json)
        posts = (detail.get("value") or {}).get("postings") or []
        by_num: dict[str, float] = {}
        for p in posts:
            if not isinstance(p, dict):
                continue
            acc = p.get("account")
            if not isinstance(acc, dict):
                continue
            num = acc.get("number")
            ag = p.get("amountGross")
            if num is None or ag is None:
                continue
            key = str(num)
            by_num[key] = by_num.get(key, 0.0) + abs(float(ag))
        if not by_num:
            _fail(f"mini-aggregate: no account totals from postings")
            err += 1
        else:
            _ok(f"mini-aggregate |amountGross| by account: {by_num}")
    except Exception as e:
        _fail(f"mini-aggregate: {e}")
        err += 1

    # POST /project (minimal valid)
    suffix = str(int(time.time()))[-8:]
    name = f"SBX ledger-probe {suffix}"
    emp = api.get("/employee", params={"from": 0, "count": 1, "fields": "id,firstName,lastName"})
    emps = emp.get("values") or []
    eid = emps[0].get("id") if emps else None
    body: dict = {"name": name, "startDate": today.isoformat()}
    if eid is not None:
        body["projectManager"] = {"id": eid}
    try:
        prj = api.post("/project", body)
        pval = prj.get("value") if isinstance(prj, dict) else None
        pidp = pval.get("id") if isinstance(pval, dict) else None
        _ok(f"POST /project → id={pidp} name={name!r}")
    except Exception as e:
        _fail(f"POST /project: {e}")
        err += 1

    return err


def run_invoice_fee_probe(api: TripletexAPI) -> int:
    """
    Covers the French reminder-fee failure mode: illegal invoice list fields, AR journal + customer merge,
    GL sanity, and outstanding snapshot for :payment discipline.
    """
    err = 0
    iid_from_list: Optional[int] = None

    # 1) execute_tool: dueDate / isPaid stripped → invoiceDueDate (no 400)
    try:
        tool_out = execute_tool(
            "tripletex_get",
            {
                "path": "/invoice",
                "params": {
                    "invoiceDateFrom": "2000-01-01",
                    "invoiceDateTo": "2099-12-31",
                    "from": 0,
                    "count": 10,
                    "fields": "id,invoiceNumber,invoiceDate,dueDate,customer,isPaid",
                },
            },
            api,
        )
        data = json.loads(tool_out)
        if data.get("http_error"):
            _fail(f"invoice list after sanitize: HTTP {data.get('http_error')} {data.get('details', '')[:200]}")
            err += 1
        else:
            note = str(data.get("_toolNote") or "")
            if "GET /invoice list" not in note:
                _fail(f"expected _toolNote from invoice fields sanitize, got {note!r}")
                err += 1
            else:
                _ok("GET /invoice list: sanitizer maps dueDate→invoiceDueDate; drops isPaid (see _toolNote)")
            vals0 = data.get("values") or []
            if vals0 and isinstance(vals0[0], dict) and vals0[0].get("id") is not None:
                try:
                    iid_from_list = int(vals0[0]["id"])
                except (TypeError, ValueError):
                    iid_from_list = None
    except Exception as e:
        _fail(f"invoice fee probe (list): {e}")
        err += 1

    if iid_from_list is None:
        try:
            inv_pick = api.get(
                "/invoice",
                params={
                    "invoiceDateFrom": "2000-01-01",
                    "invoiceDateTo": "2099-12-31",
                    "from": 0,
                    "count": 1,
                    "fields": "id",
                },
            )
            row0 = (inv_pick.get("values") or [{}])[0]
            if isinstance(row0, dict) and row0.get("id") is not None:
                iid_from_list = int(row0["id"])
        except Exception:
            pass

    # 1b) GET /invoice/{id}: amountIncludingVat in fields caused 400 on sandbox (last_solve) — strip before HTTP
    if iid_from_list is not None:
        try:
            tool_b = execute_tool(
                "tripletex_get",
                {
                    "path": f"/invoice/{iid_from_list}",
                    "params": {
                        "fields": "id,invoiceNumber,amountIncludingVat,amountOutstanding,invoiceDueDate"
                    },
                },
                api,
            )
            db = json.loads(tool_b)
            if db.get("http_error"):
                _fail(
                    f"GET /invoice/{{id}} after sanitize: HTTP {db.get('http_error')} "
                    f"{str(db.get('details', ''))[:220]}"
                )
                err += 1
            else:
                nb = str(db.get("_toolNote") or "")
                if "GET /invoice/{id}" not in nb and "InvoiceDTO detail" not in nb:
                    _fail(f"expected detail _toolNote for stripped fields, got {nb!r}")
                    err += 1
                else:
                    _ok(
                        "GET /invoice/{id}: drops amountIncludingVat from fields (no 400); see _toolNote"
                    )
        except Exception as e:
            _fail(f"invoice fee probe (detail fields): {e}")
            err += 1
    else:
        _ok("GET /invoice/{id} detail fields sanitize — skipped (no invoices in tenant)")

    def acc_row(num: str) -> Optional[dict]:
        try:
            r = api.get("/ledger/account", params={"number": num, "fields": "id,number,name"})
        except requests.HTTPError:
            return None
        vals = r.get("values") or []
        return vals[0] if vals else None

    # 2) Manual voucher: debit 1500 / credit 3400 with merged customer (Kunde mangler fix)
    try:
        cust = api.get("/customer", params={"from": 0, "count": 1, "fields": "id,name"})
        cvals = cust.get("values") or []
        cid = cvals[0].get("id") if cvals else None
    except Exception as e:
        _fail(f"customer sample: {e}")
        return err + 1

    a150 = acc_row("1500")
    a340 = acc_row("3400")
    if cid is None or not a150 or not a340:
        _fail("invoice-fee probe: need customer + accounts 1500 & 3400")
        return err + 1

    try:
        res = post_voucher_two_step(
            api,
            date=date.today().isoformat(),
            description="Sandbox --invoice-fee-probe (1500/3400 + customer on debit)",
            postings_lines=[
                {"row": 1, "account": {"id": a150["id"]}, "amountGross": 1},
                {"row": 2, "account": {"id": a340["id"]}, "amountGross": -1},
            ],
            send_to_ledger=False,
            shell_extras={"customer": {"id": cid}},
        )
        if res.get("error"):
            _fail(f"AR voucher w/ customer merge: {res.get('error')}")
            err += 1
        elif res.get("http_error"):
            _fail(f"AR voucher: {res}")
            err += 1
        else:
            _ok(f"post_voucher 1500/3400 + shell_extras.customer → voucherId={res.get('voucherId')}")
    except Exception as e:
        _fail(f"AR voucher probe: {e}")
        err += 1

    # 3) Account labels (1500 / 3400 / optional purring candidates)
    for num in ("1500", "3400", "8040", "8050"):
        row = acc_row(num)
        if row:
            _ok(f"ledger/account {num} → {row.get('name', '')[:60]!r}")
        else:
            _ok(f"ledger/account {num} — (not in chart)")

    # 4) amountOutstanding on first invoice (when any exist)
    try:
        invl = api.get(
            "/invoice",
            params={
                "invoiceDateFrom": "2000-01-01",
                "invoiceDateTo": "2099-12-31",
                "from": 0,
                "count": 1,
                "fields": "id",
            },
        )
        iv0 = (invl.get("values") or [{}])[0]
        iid = iv0.get("id") if isinstance(iv0, dict) else None
    except Exception as e:
        _fail(f"invoice pick for outstanding: {e}")
        err += 1
        iid = None

    if iid:
        try:
            inv = api.get(
                f"/invoice/{iid}",
                params={"fields": "id,invoiceNumber,amountOutstanding,amountCurrencyOutstanding"},
            )
            val = inv.get("value") or {}
            _ok(
                f"GET /invoice/{{id}} outstanding — amountOutstanding={val.get('amountOutstanding')!r} "
                f"(use for :payment paidAmount, not arbitrary partials)"
            )
        except Exception as e:
            _fail(f"invoice outstanding GET: {e}")
            err += 1
    else:
        _ok("no invoices — skipped amountOutstanding sample")

    # 5) :payment discipline (runtime clamp/block/warn — _apply_invoice_payment_paid_amount_guard)
    _ok(":payment — use GET amountOutstanding; agent clamps FCY×rate mistakes by default (TRIPLETEX_PAYMENT_PAIDAMOUNT_ACTION)")

    return err


def run_month_end_probe(api: TripletexAPI) -> int:
    """
    Month-end run regression: `fields` with bare **postings** must not 400 when execute_tool appends postings(...).
    Also smoke **GET /ledger/account** for typical periodisering / avskrivning numbers.
    """
    err = 0
    today = date.today()
    m0, m1 = _month_bounds(today)
    try:
        lst = api.get(
            "/ledger/voucher",
            params={
                "dateFrom": m0.isoformat(),
                "dateTo": m1.isoformat(),
                "from": 0,
                "count": 30,
                "fields": "id,date,description,number",
            },
        )
        vals = lst.get("values") or []
        vid = vals[-1].get("id") if vals else None
    except Exception as e:
        _fail(f"month-end probe voucher list: {e}")
        return 1

    if vid is None:
        _ok("month-end probe: no vouchers in current month — skip bare-postings test")
    else:
        try:
            out = execute_tool(
                "tripletex_get",
                {
                    "path": f"/ledger/voucher/{vid}",
                    "params": {"fields": "id,date,description,postings"},
                },
                api,
            )
            data = json.loads(out)
            if data.get("http_error"):
                _fail(
                    f"bare postings in fields → expected fix, got HTTP {data.get('http_error')} "
                    f"{str(data.get('details', ''))[:280]}"
                )
                err += 1
            else:
                posts = (data.get("value") or {}).get("postings") or []
                rich = any(
                    isinstance(p, dict)
                    and isinstance(p.get("account"), dict)
                    and p.get("amountGross") is not None
                    for p in posts
                )
                if posts and not rich:
                    _fail(f"voucher {vid} postings not expanded: {json.dumps(posts[0], ensure_ascii=False)[:200]}")
                    err += 1
                else:
                    _ok(
                        f"execute_tool GET /ledger/voucher/{{id}} with bare postings in fields → OK (voucher {vid})"
                    )
        except Exception as e:
            _fail(f"month-end probe execute_tool: {e}")
            err += 1

    for num, tag in (("1710", "forskot"), ("1290", "driftsmiddel"), ("6010", "avskrivning transport")):
        try:
            r = api.get("/ledger/account", params={"number": num, "fields": "id,number,name"})
            rows = r.get("values") or []
            if rows:
                _ok(f"GL {num} ({tag}): {rows[0].get('name', '')[:55]!r}")
            else:
                _ok(f"GL {num} — not in chart")
        except Exception as e:
            _fail(f"GL {num}: {e}")
            err += 1

    return err


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
    parser.add_argument(
        "--supplier-invoice",
        action="store_true",
        help="After basic checks: POST /customer (test supplier) + POST /supplierInvoice",
    )
    parser.add_argument(
        "--voucher",
        action="store_true",
        help="After basic checks: post_voucher_two_step — 2-line (default 6800/2900; env: TRIPLETEX_VOUCHER_DEBIT/CREDIT_NUMBER)",
    )
    parser.add_argument(
        "--voucher-vat",
        action="store_true",
        help="After basic checks: 3-line MVA journal (env: TRIPLETEX_VOUCHER_EXPENSE/VAT/AP_NUMBER)",
    )
    parser.add_argument(
        "--ledger-probe",
        action="store_true",
        help="After basic checks: ledger list/detail/posting GET + mini aggregate + POST /project",
    )
    parser.add_argument(
        "--invoice-fee-probe",
        action="store_true",
        help="After basic checks: invoice list field sanitizer, 1500/3400 voucher + customer, GL + outstanding",
    )
    parser.add_argument(
        "--month-end-probe",
        action="store_true",
        help="After basic checks: voucher detail bare postings (no duplicate 400) + GL 1710/1290/6010",
    )
    parser.add_argument(
        "--dimension-voucher",
        action="store_true",
        help="After basic checks: POST accountingDimensionName/Value + 2-line journal w/ accountingDimensionValues (Task ~06)",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Run only agent unit tests (no TRIPLETEX_SESSION_TOKEN or HTTP); exit 0 if they pass",
    )
    args = parser.parse_args()

    _load_dotenv_from_repo()

    print("Agent unit tests (local)…", flush=True)
    unit_err = run_agent_unit_tests()
    if unit_err:
        print(f"\nFinished with {unit_err} error(s) (agent unit tests).", file=sys.stderr, flush=True)
        return 1

    if args.local_only:
        print("\nAll local checks passed.", flush=True)
        return 0

    base = os.environ.get("TRIPLETEX_BASE_URL", COMPETITION_BASE_URL).rstrip("/")
    token = os.environ.get("TRIPLETEX_SESSION_TOKEN", "").strip()
    if not token or token.startswith("["):
        print(
            "Set TRIPLETEX_SESSION_TOKEN to the full base64 session token (Basic password, user 0).",
            file=sys.stderr,
        )
        return 2

    print("Tripletex sandbox checks…", flush=True)
    api = TripletexAPI(base, token)
    err = run_tripletex_checks(api)

    if args.health_url.strip():
        print("Agent checks…", flush=True)
        err += run_agent_health(args.health_url.strip())

    if args.supplier_invoice:
        print("POST /supplierInvoice probe…", flush=True)
        err += run_supplier_invoice_check(api)

    if args.voucher:
        print("post_voucher_two_step probe (simple)…", flush=True)
        err += run_voucher_probe(api)

    if args.voucher_vat:
        print("post_voucher_two_step probe (3-line VAT)…", flush=True)
        err += run_voucher_probe_vat(api)

    if args.ledger_probe:
        print("Ledger + project probe…", flush=True)
        err += run_ledger_probe(api)

    if args.invoice_fee_probe:
        print("Invoice / reminder-fee probe…", flush=True)
        err += run_invoice_fee_probe(api)

    if args.month_end_probe:
        print("Month-end probe…", flush=True)
        err += run_month_end_probe(api)

    if args.dimension_voucher:
        print("Dimension + voucher probe…", flush=True)
        err += run_dimension_voucher_probe(api)

    if err:
        print(f"\nFinished with {err} error(s).", file=sys.stderr, flush=True)
        return 1
    print("\nAll checks passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
