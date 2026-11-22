import pytest

from backend.analysis.assessment import Assessor
from backend.analysis.classification import BusinessProfile, classify
from backend.analysis.peers import PeerGroup, PeerStats, oriented_percentile
from backend.analysis.scoring import score_from_assessments
from backend.tools.types import Metric


def m(key, value, unit="x", status="ok", cat="valuation"):
    return Metric(id=f"met.{key}", key=key, label=key, category=cat, value=value, unit=unit, formula="f", status=status)


def stats(key, values):
    import statistics as st

    q = st.quantiles(values, n=4, method="inclusive")
    return PeerStats(metric=key, n=len(values), median=st.median(values), p25=q[0], p75=q[2],
                     values={f"P{i}": v for i, v in enumerate(values)})


LEVELS = {"sub_industry": PeerGroup(level="sub_industry", name="Widgets", tickers=[f"P{i}" for i in range(10)]),
          "sector": PeerGroup(level="sector", name="Industrials", tickers=[f"P{i}" for i in range(30)])}


def assessor(metrics, by_level, profile=None, info=None):
    return Assessor(metrics, profile or BusinessProfile(primary="mature"), LEVELS, by_level, info or {})


def test_oriented_percentile():
    assert oriented_percentile(10, [20, 30, 40], "lower") == 1.0
    assert oriented_percentile(50, [20, 30, 40], "lower") == 0.0
    assert oriented_percentile(30, [20, 30, 40], "higher") == 0.5


def test_pe_cheap_vs_peers_is_favourable_with_explanation():
    a = assessor([m("trailing_pe", 12)], {"sub_industry": {"trailing_pe": stats("trailing_pe", [15, 18, 20, 22, 25, 30])}})
    r = a.assess("trailing_pe")
    assert r.verdict == "favourable"
    assert r.peer.level == "sub_industry" and r.peer.percentile == 1.0
    assert "Widgets" in r.explanation and "median" in r.explanation


def test_per_metric_fallback_to_sector():
    a = assessor([m("trailing_pe", 12)], {"sub_industry": {"trailing_pe": stats("trailing_pe", [15, 18, 20])},
                                          "sector": {"trailing_pe": stats("trailing_pe", [10, 11, 13, 14, 15, 16, 17, 18, 19])}})
    r = a.assess("trailing_pe")
    assert r.peer.level == "sector" and "too few valid values" in r.explanation


def test_peg_combines_peer_and_absolute():
    a = assessor([m("peg_forward", 2.5)], {"sub_industry": {"peg_forward": stats("peg_forward", [1, 1.2, 1.5, 1.8, 2.0, 2.2])}})
    r = a.assess("peg_forward")
    assert r.verdict == "unfavourable" and r.absolute.verdict == "unfavourable" and "Lynch" in r.absolute.source


def test_debt_to_equity_serviceable_override():
    ms = [m("debt_to_equity", 3.0), m("interest_coverage", 12.0), m("net_debt_to_ebitda", 1.0)]
    r = assessor(ms, {}).assess("debt_to_equity")
    assert r.verdict == "neutral" and r.overrides and "serviceable" in r.explanation


def test_utility_has_higher_leverage_band():
    r = assessor([m("debt_to_equity", 1.4)], {}, BusinessProfile(primary="utility")).assess("debt_to_equity")
    assert r.verdict == "favourable"
    r2 = assessor([m("debt_to_equity", 1.4)], {}).assess("debt_to_equity")
    assert r2.verdict == "neutral"


def test_bank_leverage_not_applicable():
    p = BusinessProfile(primary="financial", flags=["balance_sheet_financial"])
    r = assessor([m("debt_to_equity", 9.0)], {}, p).assess("debt_to_equity")
    assert r.verdict == "not_applicable"


def test_growth_company_premium_pe_neutralised_by_peg():
    by = {"sub_industry": {"trailing_pe": stats("trailing_pe", [10, 12, 14, 15, 16, 18]),
                           "peg_forward": stats("peg_forward", [1.5, 1.8, 2.0, 2.2, 2.5, 3.0])}}
    ms = [m("trailing_pe", 40), m("peg_forward", 0.9)]
    r = assessor(ms, by, BusinessProfile(primary="growth", flags=["growth"])).assess("trailing_pe")
    assert r.verdict == "neutral" and "Growth company" in r.explanation


def test_unavailable_and_informational():
    a = assessor([m("trailing_pe", None, status="not_meaningful"), m("roa_ttm", 0.1, "pct")], {})
    assert a.assess("trailing_pe").verdict == "not_meaningful"
    assert a.assess("roa_ttm").verdict == "informational"


def test_classification():
    ms = [Metric(id="x", key="revenue_cagr_3y", label="", category="growth", value=0.35, unit="pct", formula="")]
    p = classify("Information Technology", "Semiconductors", ms, 100, 50)
    assert p.primary == "growth" and "high_growth" in p.flags and "cyclical" in p.flags
    p2 = classify("Financials", "Regional Banks", [], 100, 50)
    assert p2.primary == "financial" and "balance_sheet_financial" in p2.flags
    p3 = classify("Consumer Discretionary", "Restaurants", [], -5, -10)
    assert "unprofitable" in p3.flags and "negative_equity" in p3.flags


def test_rubric_score_excludes_not_applicable_and_reports_coverage():
    by = {"sub_industry": {"trailing_pe": stats("trailing_pe", [15, 18, 20, 22, 25, 30])}}
    a = assessor([m("trailing_pe", 12), m("forward_pe", 50)], by)
    s = score_from_assessments("valuation", a.assess_all(["trailing_pe", "forward_pe"]))
    mult = next(d for d in s.dimensions if d.name == "multiples")
    assert mult.score == pytest.approx(10.0)  # forward_pe informational (no peers) => excluded from points
    assert s.coverage < 1.0 and s.unavailable_dimensions
