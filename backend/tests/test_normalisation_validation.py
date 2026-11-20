from datetime import date

import pandas as pd
import pytest

from backend.data.normalisation import (
    FIELDS,
    build_financials,
    compare,
    fiscal_label,
    sec_field_series,
)
from backend.data.provenance import PeriodType, Quality
from backend.tests.fixtures import default_companies


def test_compare_bands():
    assert compare(100.5, 100)[1] == "match"
    assert compare(103, 100)[1] == "minor_difference"
    assert compare(120, 100)[1] == "conflict"


def test_fiscal_labels_including_52_53_week_years():
    assert fiscal_label(date(2026, 9, 30), 6, PeriodType.QUARTERLY) == "Q1 FY2027"
    assert fiscal_label(date(2026, 6, 30), 6, PeriodType.ANNUAL) == "FY2026"
    assert fiscal_label(date(2026, 12, 27), 9, PeriodType.QUARTERLY) == "Q1 FY2027"
    assert fiscal_label(date(2027, 1, 3), 12, PeriodType.QUARTERLY) == "Q4 FY2026"  # 53-week year ending early January


def test_sec_ytd_cash_flow_quarters_are_derived():
    c = default_companies()["TSTA"]
    annual, quarterly = sec_field_series(c.facts(), FIELDS["operating_cash_flow"])
    derived = [f for f in quarterly.values() if f.derived]
    assert derived, "Q2–Q4 should be derived from year-to-date cumulative values"
    end = sorted(quarterly)[-2]
    idx = c.qends.index(end)
    assert quarterly[end].value == pytest.approx(c.q(idx)["operating_cash_flow"], rel=1e-6)


def test_restatement_detected_with_split_hint():
    facts = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": [
        {"start": "2019-01-01", "end": "2019-12-31", "val": 12.0, "accn": "a", "form": "10-K", "filed": "2020-02-01"},
        {"start": "2019-01-01", "end": "2019-12-31", "val": 3.0, "accn": "b", "form": "10-K", "filed": "2021-02-01"},
    ]}}}}}
    annual, _ = sec_field_series(facts, FIELDS["diluted_eps"])
    f = annual[date(2019, 12, 31)]
    assert f.value == 3.0 and f.original_value == 12.0
    assert "split" in (f.derivation or "")


def test_point_in_time_filter_excludes_later_filings():
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"start": "2019-01-01", "end": "2019-12-31", "val": 10.0, "accn": "a", "form": "10-K", "filed": "2020-02-01"},
        {"start": "2020-01-01", "end": "2020-12-31", "val": 20.0, "accn": "b", "form": "10-K", "filed": "2021-02-01"},
    ]}}}}}
    annual, _ = sec_field_series(facts, FIELDS["net_income"], as_of=date(2020, 6, 1))
    assert list(annual) == [date(2019, 12, 31)]


def test_cross_check_conflict_uses_sec_and_preserves_vendor():
    c = default_companies()["TSTX"]  # vendor revenue understated 20% in the prior FY
    fd = build_financials(c.ticker, c.statements(), c.facts(), c.cik, "USD")
    latest = [p for p in fd.annual["revenue"] if p.value is not None][-2]
    assert latest.quality == Quality.CONFLICT
    assert latest.source_type.value == "regulatory_filing"
    assert latest.original_value == pytest.approx(latest.value * 0.8)
    assert any("source-priority" in n for n in latest.notes)


def test_vendor_only_values_marked_unverified():
    c = default_companies()["TSTA"]
    fd = build_financials(c.ticker, c.statements(), None, None, "USD")
    assert fd.latest("revenue", "annual").quality == Quality.UNVERIFIED
    assert any("unavailable" in n for n in fd.notes)


def test_missing_vendor_value_is_not_zero():
    c = default_companies()["TSTA"]
    st = c.statements()
    st.income_quarterly.loc["Total Revenue", st.income_quarterly.columns[0]] = float("nan")
    fd = build_financials(c.ticker, st, None, None, "USD")
    p = fd.latest("revenue", "quarterly")
    assert fd.quarterly["revenue"][-1].value is None
    assert fd.quarterly["revenue"][-1].quality == Quality.MISSING
    assert p is not None and p.value != 0


def _ingest(providers, t):
    from backend.data.ingestion import CompanyIdentity, ingest_company

    c = providers.market.cos[t]
    return ingest_company(providers, CompanyIdentity(ticker=t, name=c.name, cik=c.cik, sector=c.sector, sub_industry=c.sub))


def test_validation_good_and_failed_sources(providers):
    from backend.data.validation import validate

    dq = validate(_ingest(providers, "TSTA"))
    assert dq.status in ("good", "acceptable")
    assert not dq.missing_critical_fields
    providers.market.fail.add("TSTB")
    res = _ingest(providers, "TSTB")
    dq2 = validate(res)
    assert dq2.status == "critical"
    assert dq2.failed_sources and dq2.blocks_buy_now
    assert any(i.code == "no_prices" for i in dq2.issues)


def test_validation_stale_prices(providers):
    from backend.data.validation import validate

    res = _ingest(providers, "TSTA")
    res.prices = res.prices.iloc[:-30]
    dq = validate(res)
    assert any(i.code == "stale_prices" for i in dq.issues)


def test_validation_flags_non_equity(providers):
    from backend.data.validation import validate

    res = _ingest(providers, "TSTA")
    res.info["quoteType"] = "ETF"
    dq = validate(res)
    assert dq.status == "critical"


def test_sp500_parser_rejects_partial_table():
    from backend.data.providers.sp500 import parse_constituents_html

    html = "<table id='constituents'><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th></tr>" \
           "<tr><td>AAA</td><td>A</td><td>X</td><td>Y</td></tr></table>"
    with pytest.raises(ValueError):
        parse_constituents_html(html)
    rows = "".join(f"<tr><td>T{i}</td><td>Co {i}</td><td>S</td><td>SI</td></tr>" for i in range(500))
    df = parse_constituents_html("<table id='constituents'><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th>"
                                 f"<th>GICS Sub-Industry</th></tr>{rows}</table>")
    assert len(df) == 500 and list(df.columns)[:4] == ["symbol", "name", "sector", "sub_industry"]


def test_yahoo_symbol_mapping():
    from backend.data.providers.yfinance_provider import to_yahoo_symbol

    assert to_yahoo_symbol("brk.b") == "BRK-B"
    _ = pd  # keep import used


def test_split_adjustment_of_pre_split_filings():
    from backend.data.ingestion import adjust_for_splits
    from backend.data.normalisation import FinancialData
    from backend.data.provenance import DataPoint

    fd = FinancialData(ticker="X", currency="USD", fye_month=12)
    fd.annual["diluted_eps"] = [
        DataPoint(id="a", field="diluted_eps", label="EPS", value=12.0, unit="USD/share", period_type="annual",
                  period_end=date(2017, 12, 31), filing_date=date(2018, 2, 1), source_name="SEC", source_type="regulatory_filing"),
        DataPoint(id="b", field="diluted_eps", label="EPS", value=4.0, unit="USD/share", period_type="annual",
                  period_end=date(2021, 12, 31), filing_date=date(2022, 2, 1), source_name="SEC", source_type="regulatory_filing")]
    px = pd.DataFrame({"Close": [1.0, 1.0], "Stock Splits": [0.0, 4.0]}, index=pd.to_datetime(["2020-08-28", "2020-08-31"]))
    assert adjust_for_splits(fd, px) == 1
    assert fd.annual["diluted_eps"][0].value == 3.0 and fd.annual["diluted_eps"][0].original_value == 12.0
    assert fd.annual["diluted_eps"][1].value == 4.0


def test_scale_mismatch_and_definition_difference():
    from backend.data.normalisation import compare, scale_mismatch

    assert scale_mismatch(741_300_000, 741.3) == 1e6
    assert compare(741_300_000, 741.3)[1] == "unit_scale_mismatch"
    c = default_companies()["TSTA"]
    st = c.statements()
    st.income_annual.loc["Selling General And Administration"] *= 1.3  # vendor defines SG&A differently
    fd = build_financials(c.ticker, st, c.facts(), c.cik, "USD")
    assert not [x for x in fd.cross_checks if x["field"] == "sga" and x["status"] == "conflict"]


def test_revenue_takes_largest_concept_per_period():
    facts = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            {"start": "2023-01-01", "end": "2023-12-31", "val": 24.8, "accn": "a", "form": "10-K", "filed": "2024-02-01"}]}},
        "Revenues": {"units": {"USD": [
            {"start": "2023-01-01", "end": "2023-12-31", "val": 28.1, "accn": "a", "form": "10-K", "filed": "2024-02-01"}]}}}}}
    annual, _ = sec_field_series(facts, FIELDS["revenue"])
    assert annual[date(2023, 12, 31)].value == 28.1
