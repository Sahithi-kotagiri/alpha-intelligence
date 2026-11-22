"""Evidence pack: every datum an agent may cite, with a stable ID and full provenance.

Agents receive role-scoped subsets and may only cite IDs that exist in their subset.
Text that originates outside the system (news headlines/summaries, filing titles) is
flagged `untrusted` and rendered inside delimiters: it is evidence, never instructions.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from backend.analysis.assessment import Assessment
from backend.data.ingestion import EIGHT_K_ITEMS, IngestionResult
from backend.data.provenance import DataPoint
from backend.data.validation import DataQualityReport
from backend.tools.types import Metric, fmt_value


class EvidenceItem(BaseModel):
    id: str
    category: str
    title: str
    value: float | None = None
    unit: str | None = None
    display: str = ""
    date: str | None = None
    period: str | None = None
    source_name: str
    source_url: str | None = None
    source_type: str
    quality: str = "ok"
    untrusted_text: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class EvidencePack(BaseModel):
    items: dict[str, EvidenceItem] = Field(default_factory=dict)

    def add(self, item: EvidenceItem) -> None:
        self.items[item.id] = item

    def get(self, eid: str) -> EvidenceItem | None:
        return self.items.get(eid)

    def ids(self) -> set[str]:
        return set(self.items)

    def by_category(self, *cats: str) -> list[EvidenceItem]:
        return [i for i in self.items.values() if i.category in cats]


def _clean(v: Any) -> Any:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def from_datapoint(p: DataPoint) -> EvidenceItem:
    return EvidenceItem(
        id=p.id, category="financial", title=f"{p.label} — {p.fiscal_period or p.period_end}", value=p.value, unit=p.unit,
        display=fmt_value(p.value, p.unit), date=p.period_end.isoformat() if p.period_end else None, period=p.fiscal_period,
        source_name=p.source_name, source_url=p.source_url, source_type=str(p.source_type.value if hasattr(p.source_type, "value") else p.source_type),
        quality=str(p.quality.value if hasattr(p.quality, "value") else p.quality),
        detail={"period_type": str(p.period_type), "filing_date": p.filing_date.isoformat() if p.filing_date else None,
                "retrieved_at": p.retrieved_at.isoformat() if p.retrieved_at else None,
                "reported_or_calculated": p.reported_or_calculated, "formula": p.calculation_formula, "inputs": p.inputs,
                "notes": p.notes, "original_value": p.original_value, "original_source": p.original_source,
                "cross_check": p.cross_check.model_dump(mode="json") if p.cross_check else None,
                "accession": p.extra.get("accession")},
    )


def from_metric(m: Metric) -> EvidenceItem:
    return EvidenceItem(
        id=m.id, category="metric" if m.category not in ("valuation", "valuation_history") else "valuation",
        title=f"{m.label}{' — ' + m.period if m.period else ''}", value=m.value, unit=m.unit,
        display=fmt_value(m.value, m.unit) if m.ok else f"{m.status.replace('_', ' ')}: {m.reason}",
        date=m.period_end.isoformat() if m.period_end else None, period=m.period, source_name=m.source,
        source_type="calculated", quality="ok" if m.ok else m.status,
        detail={"key": m.key, "metric_category": m.category, "formula": m.formula, "inputs": m.inputs, "status": m.status,
                "reason": m.reason, "notes": m.notes, "higher_is_better": m.higher_is_better})


def from_assessment(a: Assessment) -> EvidenceItem:
    return EvidenceItem(
        id=f"asm.{a.metric_key}", category="assessment", title=f"Assessment: {a.label}", value=a.value, unit=a.unit,
        display=f"{a.display} → {a.verdict}", period=a.period, source_name="Threshold engine " + a.threshold_version,
        source_type="calculated", quality="ok",
        detail={"verdict": a.verdict, "explanation": a.explanation, "peer": a.peer.model_dump() if a.peer else None,
                "absolute": a.absolute.model_dump() if a.absolute else None, "overrides": a.overrides,
                "metric_id": a.metric_id, "evidence_ids": a.evidence_ids})


def build_pack(res: IngestionResult, dq: DataQualityReport, metrics: list[Metric], assessments: list[Assessment],
               technicals: dict[str, Any] | None, risk: dict[str, Any] | None, peer_info: dict[str, Any],
               snapshot: Any, scenarios: dict[str, Any] | None, portfolio: dict[str, Any] | None) -> EvidencePack:
    pack = EvidencePack()
    ident = res.identity
    pack.add(EvidenceItem(id="id.company", category="identity", title=f"{ident.name} ({ident.ticker})",
                          display=f"{ident.exchange or ''} · {ident.sector or 'n/a'} / {ident.sub_industry or 'n/a'} · CIK {ident.cik}",
                          source_name="S&P 500 constituents (Wikipedia) + SEC ticker map + Yahoo profile",
                          source_type="index_constituents",
                          detail={"in_sp500": ident.in_sp500, "currency": ident.currency, "country": ident.country,
                                  "employees": ident.employees}))
    if ident.description:
        pack.add(EvidenceItem(id="id.business_description", category="identity", title="Business description (vendor profile)",
                              source_name="Yahoo Finance profile", source_type="market_data_vendor",
                              untrusted_text=ident.description[:1500]))
    pack.add(EvidenceItem(id="dq.report", category="data_quality", title="Data-quality report", display=dq.status,
                          source_name="Validation layer", source_type="calculated",
                          detail=dq.model_dump(mode="json", exclude={"issues"}) | {"issues": [i.model_dump() for i in dq.issues[:25]]}))
    # financial data points (keep last 6 annual + 8 quarterly per field + TTM)
    fin = res.financials
    if fin:
        for _fld, pts in fin.annual.items():
            for p in pts[-6:]:
                pack.add(from_datapoint(p))
        for _fld, pts in fin.quarterly.items():
            for p in pts[-8:]:
                pack.add(from_datapoint(p))
        for p in fin.ttm.values():
            pack.add(from_datapoint(p))
    for m in metrics:
        if m.key.endswith("_quarterly") or m.key.endswith("_qoq"):
            if m.period_end and fin and fin.quarterly.get("revenue"):
                ends = sorted({p.period_end for p in fin.quarterly["revenue"]})[-6:]
                if m.period_end not in ends:
                    continue
        pack.add(from_metric(m))
    for a in assessments:
        pack.add(from_assessment(a))
    # price snapshot
    if snapshot and snapshot.price is not None:
        pack.add(EvidenceItem(id=snapshot.price_id, category="price", title=f"Closing price {snapshot.price_date}",
                              value=snapshot.price, unit="USD/share", display=f"${snapshot.price:,.2f}",
                              date=str(snapshot.price_date), source_name="Yahoo Finance via yfinance (daily close)",
                              source_type="market_data_vendor"))
        if snapshot.shares_outstanding:
            pack.add(EvidenceItem(id=snapshot.shares_id, category="price", title="Shares outstanding",
                                  value=snapshot.shares_outstanding, unit="shares",
                                  display=fmt_value(snapshot.shares_outstanding, "shares"), source_name=snapshot.shares_source,
                                  source_type="market_data_vendor"))
    # technicals / risk
    if technicals:
        through = technicals.get("data_through")
        for k, v in technicals.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                pack.add(EvidenceItem(id=f"tech.{k}", category="technical", title=k.replace("_", " "), value=_clean(float(v)),
                                      display=f"{v:,.4g}", date=through, source_name="Calculated from daily prices",
                                      source_type="calculated"))
            elif k in ("trend_short", "trend_medium", "trend_long", "breakout", "golden_cross_state"):
                pack.add(EvidenceItem(id=f"tech.{k}", category="technical", title=k.replace("_", " "), display=str(v),
                                      date=through, source_name="Calculated from daily prices", source_type="calculated"))
        for grp in ("returns", "rs_benchmark", "rs_sector", "benchmark_returns", "sector_returns"):
            for w, v in (technicals.get(grp) or {}).items():
                if v is not None:
                    pack.add(EvidenceItem(id=f"tech.{grp}.{w}", category="technical", title=f"{grp.replace('_', ' ')} {w}",
                                          value=v, unit="pct", display=fmt_value(v, "pct"), date=through,
                                          source_name="Calculated from adjusted closes", source_type="calculated"))
        for i, z in enumerate(technicals.get("levels") or []):
            pack.add(EvidenceItem(id=f"tech.level.{i}", category="technical", title=f"{z['type'].title()} zone",
                                  display=f"${z['low']:,.2f}–${z['high']:,.2f} ({z['touches']} touches, last {z['last_touch']})",
                                  value=(z["low"] + z["high"]) / 2, unit="USD/share", date=through,
                                  source_name="Swing-point clustering (documented method)", source_type="calculated", detail=z))
    if risk:
        through = risk.get("data_through")
        for k, v in risk.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                pack.add(EvidenceItem(id=f"risk.{k}", category="risk", title=k.replace("_", " "), value=_clean(float(v)),
                                      display=f"{v:,.4g}", date=through, source_name="Calculated from daily prices",
                                      source_type="calculated"))
        if risk.get("gaps"):
            pack.add(EvidenceItem(id="risk.gaps", category="risk", title="Opening gap statistics (2y)",
                                  display=str(risk["gaps"]), source_name="Calculated", source_type="calculated", detail=risk["gaps"]))
    if scenarios and scenarios.get("available"):
        for k, s in scenarios["scenarios"].items():
            pack.add(EvidenceItem(id=f"scn.{k}", category="scenario", title=f"{k.title()} scenario ({scenarios['method']})",
                                  value=s.get("price"), unit="USD/share",
                                  display=f"${s.get('price')} ({fmt_value(s.get('return'), 'pct')})",
                                  source_name="Scenario model (illustrative assumptions)", source_type="calculated",
                                  quality="estimate", detail=s | {"basis": scenarios.get("eps_basis"),
                                                                  "multiple_basis": scenarios.get("multiple_basis")}))
    # vendor reference fields
    v = snapshot.vendor if snapshot else {}
    gov = {k: v.get(k) for k in ("auditRisk", "boardRisk", "compensationRisk", "shareHolderRightsRisk", "overallRisk") if v.get(k) is not None}
    if gov:
        pack.add(EvidenceItem(id="vendor.governance", category="governance", title="Governance risk scores (ISS via Yahoo; 1 = low, 10 = high)",
                              display=", ".join(f"{k}: {x}" for k, x in gov.items()), source_name="Yahoo Finance (ISS QualityScore)",
                              source_type="market_data_vendor", detail=gov))
    own = {k: v.get(k) for k in ("heldPercentInsiders", "heldPercentInstitutions", "shortPercentOfFloat") if v.get(k) is not None}
    if own:
        pack.add(EvidenceItem(id="vendor.ownership", category="governance", title="Ownership and short interest",
                              display=", ".join(f"{k}: {fmt_value(x, 'pct')}" for k, x in own.items()),
                              source_name="Yahoo Finance", source_type="market_data_vendor", detail=own))
    # estimates (labelled)
    from backend.tools.valuation import consensus_growth

    g, eps_next, note = consensus_growth(res.estimates)
    if eps_next is not None:
        pack.add(EvidenceItem(id="est.eps_next_fy", category="estimate", title="Consensus EPS, next fiscal year",
                              value=eps_next, unit="USD/share", display=f"${eps_next:.2f}", source_name=note or "Yahoo consensus",
                              source_type="consensus_estimate", quality="estimate"))
    if g is not None:
        pack.add(EvidenceItem(id="est.eps_growth_next_fy", category="estimate", title="Consensus EPS growth, next fiscal year",
                              value=g, unit="pct", display=fmt_value(g, "pct"), source_name=note or "Yahoo consensus",
                              source_type="consensus_estimate", quality="estimate"))
    for k, title in (("targetMeanPrice", "Analyst mean price target"), ("recommendationMean", "Analyst recommendation mean (1=strong buy, 5=sell)"),
                     ("numberOfAnalystOpinions", "Number of analyst opinions")):
        if v.get(k) is not None:
            pack.add(EvidenceItem(id=f"est.{k}", category="estimate", title=title, value=_clean(float(v[k])),
                                  display=f"{v[k]}", source_name="Yahoo Finance consensus", source_type="consensus_estimate",
                                  quality="estimate"))
    # calendar
    cal = res.calendar or {}
    if cal.get("Earnings Date"):
        ed = cal["Earnings Date"]
        ed = ed[0] if isinstance(ed, list) and ed else ed
        pack.add(EvidenceItem(id="cal.next_earnings", category="calendar", title="Next earnings date (vendor calendar)",
                              display=str(ed), date=str(ed), source_name="Yahoo Finance calendar", source_type="market_data_vendor",
                              quality="unverified",
                              detail={"note": "Vendor calendar date; confirm with company investor relations.",
                                      "eps_estimate_avg": _clean(cal.get("Earnings Average"))}))
    # earnings surprises
    eh = res.earnings_history
    if isinstance(eh, pd.DataFrame) and not eh.empty:
        for idx, row in eh.tail(8).iterrows():
            d = str(idx)[:10]
            act, est = _clean(row.get("epsActual")), _clean(row.get("epsEstimate"))
            sp = _clean(row.get("surprisePercent"))
            pack.add(EvidenceItem(id=f"eh.{d}", category="earnings_history", title=f"EPS vs consensus, quarter ending {d}",
                                  value=sp, unit="pct", display=f"actual {act} vs est {est} ({fmt_value(sp, 'pct')} surprise)",
                                  date=d, source_name="Yahoo Finance earnings history", source_type="market_data_vendor",
                                  detail={"eps_actual": act, "eps_estimate": est, "surprise_pct": sp}))
    # analyst actions (last 12 months, max 15)
    aa = res.analyst_actions
    if isinstance(aa, pd.DataFrame) and not aa.empty:
        aa = aa.sort_index(ascending=False).head(15)
        for i, (idx, row) in enumerate(aa.iterrows()):
            d = str(idx)[:10]
            pack.add(EvidenceItem(id=f"aa.{i}", category="analyst_action",
                                  title=f"{row.get('Firm')}: {row.get('Action')} {row.get('FromGrade') or ''}→{row.get('ToGrade')}",
                                  display=f"PT {row.get('priorPriceTarget')}→{row.get('currentPriceTarget')}" if 'currentPriceTarget' in row else "",
                                  date=d, source_name="Yahoo Finance upgrades/downgrades", source_type="market_data_vendor",
                                  detail={k: _clean(row.get(k)) for k in row.index}))
    # news (deduplicated by normalised title)
    seen: set[str] = set()
    n = 0
    for item in res.news:
        key = "".join(ch for ch in item.title.lower() if ch.isalnum())[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        pack.add(EvidenceItem(id=f"news.{n}", category="news", title=f"News headline ({item.publisher or 'unknown publisher'})",
                              date=(item.published_at or "")[:10] or None, source_name=item.publisher or item.source_name,
                              source_url=item.url, source_type="news", quality="unverified",
                              untrusted_text=f"{item.title}\n{(item.summary or '')[:600]}",
                              detail={"publication_date": item.published_at, "related_tickers": item.related_tickers,
                                      "feed": item.source_name, "event_date": None}))
        n += 1
    # SEC filings (8-K items mapped to materiality)
    for f in res.filings[:40]:
        form = f.get("form")
        if form not in ("8-K", "10-K", "10-Q", "8-K/A", "10-K/A", "10-Q/A", "DEF 14A", "S-1", "S-3", "4", "SC 13D"):
            continue
        if form == "4":
            continue
        items = [s.strip() for s in str(f.get("items") or "").split(",") if s.strip()]
        mapped = [{"item": it, "description": EIGHT_K_ITEMS.get(it, ("Other", "low"))[0],
                   "materiality": EIGHT_K_ITEMS.get(it, ("Other", "low"))[1]} for it in items]
        mat = "high" if any(m["materiality"] == "high" for m in mapped) else "medium" if any(m["materiality"] == "medium" for m in mapped) else "low"
        if form.startswith(("10-K", "10-Q")):
            mat = "medium"
        pack.add(EvidenceItem(id=f"sec.{form.replace('/', '').replace(' ', '')}.{f['accessionNumber']}", category="filing",
                              title=f"SEC {form} filed {f.get('filingDate')}" + (f": {', '.join(m['description'] for m in mapped)}" if mapped else ""),
                              date=f.get("filingDate"), source_name="SEC EDGAR", source_url=f.get("url"),
                              source_type="regulatory_filing",
                              detail={"form": form, "items": mapped, "materiality": mat, "report_date": f.get("reportDate"),
                                      "accession": f["accessionNumber"], "primary_document": f.get("primaryDocument")}))
    # peers
    for key, st in (peer_info.get("stats") or {}).items():
        if st.get("n", 0) >= 3 and st.get("median") is not None:
            pack.add(EvidenceItem(id=f"peer.{key}", category="peer", title=f"Peer {key.replace('_', ' ')} distribution",
                                  value=st["median"], display=f"median {st['median']:.3g}, IQR {st['p25']:.3g}–{st['p75']:.3g}, n={st['n']}",
                                  source_name=f"{peer_info.get('level')} peers: {peer_info.get('name')} (Yahoo vendor values)",
                                  source_type="market_data_vendor", detail={k: st[k] for k in ("n", "median", "p25", "p75")}))
    if portfolio and portfolio.get("available"):
        imp = portfolio.get("impact") or {}
        for k in ("vol_before", "vol_after", "hhi_before", "hhi_after", "candidate_corr_to_portfolio",
                  "max_drawdown_before", "max_drawdown_after"):
            if imp.get(k) is not None:
                pack.add(EvidenceItem(id=f"pf.{k}", category="portfolio", title=f"Portfolio {k.replace('_', ' ')}",
                                      value=imp[k], display=f"{imp[k]:.4g}", source_name="Portfolio analytics (user holdings)",
                                      source_type="user_input"))
    return pack


AGENT_SCOPES: dict[str, tuple[str, ...]] = {
    "valuation": ("identity", "rulebook", "valuation", "metric", "estimate", "peer", "data_quality"),
    "fundamental": ("identity", "rulebook", "financial", "metric", "valuation", "assessment", "peer", "estimate", "earnings_history",
                    "governance", "data_quality"),
    "technical": ("identity", "rulebook", "price", "technical", "data_quality"),
    "news": ("identity", "rulebook", "news", "filing", "analyst_action", "calendar", "estimate", "earnings_history", "data_quality"),
    "risk_portfolio": ("identity", "rulebook", "risk", "technical", "assessment", "scenario", "portfolio", "price", "data_quality", "governance"),
}


def scoped(pack: EvidencePack, agent: str, max_items: int = 600) -> list[EvidenceItem]:
    cats = AGENT_SCOPES.get(agent)
    items = list(pack.items.values()) if cats is None else [i for i in pack.items.values() if i.category in cats]
    if agent == "risk_portfolio":
        items = [i for i in items if i.category != "assessment" or i.id.split(".", 1)[1] in
                 ("debt_to_equity", "net_debt_to_ebitda", "interest_coverage", "current_ratio", "altman_z", "piotroski_f_score",
                  "trailing_pe", "peg_forward", "ev_to_ebitda")]
    return items[:max_items]


def render_for_prompt(items: list[EvidenceItem]) -> str:
    """Compact, model-readable table. Untrusted text is fenced and labelled."""
    lines = []
    for i in items:
        meta = "; ".join(x for x in [i.period or i.date or "", i.source_name, f"quality={i.quality}" if i.quality != "ok" else ""] if x)
        line = f"[{i.id}] {i.title}: {i.display} ({meta})"
        if i.category == "assessment":
            line += f"\n    explanation: {i.detail.get('explanation', '')}"
        if i.untrusted_text:
            line += f"\n    <untrusted_source_text>{i.untrusted_text}</untrusted_source_text>"
        lines.append(line)
    return "\n".join(lines)
