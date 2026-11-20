"""Output guardrails: citation validity and numeric consistency.

1. Citations: every `evidence_ids` list anywhere in an LLM output must reference IDs in
   the evidence the agent received. Invalid IDs trigger one corrective retry; any that
   remain are removed and the claim is downgraded if no valid evidence remains.
2. Numbers: numeric tokens in claim text are compared with evidence values (raw, %,
   x-multiples, $B/$M/$T). Numbers with no matching evidence value are reported as
   `unverified_numbers` and surfaced in the UI. This is a detector, not a proof.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from backend.analysis.evidence import EvidenceItem

NUM_RE = re.compile(r"(?<![\w.\-\[])(-|−)?\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?(%|x|bn|B|M|T|pp)?(?![\w\]])")


def collect_evidence_ids(obj: Any, path: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(obj, BaseModel):
        obj = obj.model_dump()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("evidence_ids", "citations") and isinstance(v, list):
                out += [(f"{path}.{k}", str(x)) for x in v]
            elif k == "evidence_id" and isinstance(v, str):
                out.append((f"{path}.{k}", v))
            else:
                out += collect_evidence_ids(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += collect_evidence_ids(v, f"{path}[{i}]")
    return out


def invalid_citations(obj: Any, allowed: set[str]) -> list[str]:
    return sorted({eid for _, eid in collect_evidence_ids(obj) if eid not in allowed})


def strip_invalid(obj: Any, allowed: set[str]) -> tuple[Any, int]:
    """Remove invalid IDs in place (on a dict copy). Returns (clean_dict, claims_left_unsupported)."""
    data = obj.model_dump() if isinstance(obj, BaseModel) else obj
    unsupported = 0

    def walk(x):
        nonlocal unsupported
        if isinstance(x, dict):
            for k in list(x.keys()):
                v = x[k]
                if k in ("evidence_ids", "citations") and isinstance(v, list):
                    before = len(v)
                    x[k] = [e for e in v if e in allowed]
                    if before and not x[k] and "text" in x:
                        x["text"] = "[Unsupported – cited evidence did not exist] " + x["text"]
                        unsupported += 1
                elif k == "evidence_id" and isinstance(v, str) and v not in allowed:
                    x[k] = None
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data)
    return data, unsupported


def _candidates(items: list[EvidenceItem]) -> list[float]:
    vals: list[float] = []
    for i in items:
        v = i.value
        if v is None:
            continue
        vals.append(abs(v))
        if i.unit == "pct":
            vals.append(abs(v * 100))
        if i.unit == "USD":
            for d in (1e12, 1e9, 1e6):
                vals.append(abs(v / d))
        if i.unit == "shares":
            vals += [abs(v / 1e6), abs(v / 1e9)]
        for key in ("median", "p25", "p75"):
            dv = i.detail.get(key) if isinstance(i.detail, dict) else None
            if isinstance(dv, (int, float)):
                vals += [abs(dv), abs(dv * 100)]
        for nested in ("peer",):
            pd_ = i.detail.get(nested) if isinstance(i.detail, dict) else None
            if isinstance(pd_, dict):
                for key in ("median", "p25", "p75", "company_value_used", "percentile"):
                    if isinstance(pd_.get(key), (int, float)):
                        vals += [abs(pd_[key]), abs(pd_[key] * 100)]
    return vals


def _parse(m: re.Match) -> tuple[float, str | None]:
    num = float((m.group(2) or "0").replace(",", "") + (m.group(3) or ""))
    return num, m.group(4)


def unverified_numbers(texts: list[str], items: list[EvidenceItem]) -> list[str]:
    cands = _candidates(items)
    bad: list[str] = []
    for t in texts:
        for m in NUM_RE.finditer(t or ""):
            num, unit = _parse(m)
            if unit is None and m.group(3) is None and (num <= 12 or 1900 <= num <= 2100):
                continue  # small counts, years, quarter numbers
            if unit == "pp":
                continue  # percentage-point differences are derived
            ok = any(abs(num - c) <= max(0.051 if num < 10 else 0.5, 0.02 * max(abs(c), 1e-9)) for c in cands)
            if not ok:
                bad.append(m.group(0).strip())
    return sorted(set(bad))


def claim_texts(obj: Any) -> list[str]:
    data = obj.model_dump() if isinstance(obj, BaseModel) else obj
    out: list[str] = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("text", "objection", "mechanism", "answer", "risk", "assessment", "resolution", "description",
                         "executive_assessment", "executive_summary", "summary") and isinstance(v, str):
                    out.append(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data)
    return out
