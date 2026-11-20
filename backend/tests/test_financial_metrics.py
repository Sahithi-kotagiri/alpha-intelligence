import math

import pytest

from backend.tools.financial_metrics import (
    FundamentalCalculator,
    altman_z,
    average,
    cagr,
    effective_tax_rate,
    growth,
    latest_metric,
    piotroski,
    roic,
    safe_div,
)


def test_safe_div_and_average():
    assert safe_div(10, 2) == 5
    assert safe_div(10, 0) is None
    assert safe_div(None, 2) is None
    assert average(2, 4) == 3
    assert average(None, 4) == 4
    assert average(None, None) is None


def test_growth_rules():
    assert growth(110, 100) == (pytest.approx(0.10), None)
    v, why = growth(50, -100)
    assert v is None and "negative" in why
    v, why = growth(50, 0)
    assert v is None
    assert growth(None, 1)[0] is None


def test_cagr():
    v, _ = cagr(100, 133.1, 3)
    assert v == pytest.approx(0.10, rel=1e-6)
    assert cagr(-1, 100, 3)[0] is None
    assert cagr(100, 0, 3)[0] is None


def test_effective_tax_rate_clamp_and_fallback():
    assert effective_tax_rate(20, 100)[0] == pytest.approx(0.20)
    assert effective_tax_rate(60, 100)[0] == pytest.approx(0.35)
    r, note = effective_tax_rate(None, None)
    assert r == 0.21 and "statutory" in note
    assert effective_tax_rate(10, -5)[0] == 0.21


def test_roic_formula_and_negative_ic():
    v, _ = roic(ebit=100, tax=20, pretax=100, debt_open=200, debt_close=200, equity_open=400, equity_close=400,
                cash_open=100, cash_close=100)
    assert v == pytest.approx(80 / 500)
    v, why = roic(100, 20, 100, 0, 0, 10, 10, 100, 100)
    assert v is None and "negative" in why


def test_piotroski_full_and_partial():
    cur = dict(net_income=10, operating_cash_flow=15, total_assets=100, long_term_debt=10, current_assets=50,
               current_liabilities=25, diluted_shares=100, gross_profit=40, revenue=100)
    prev = dict(net_income=5, operating_cash_flow=6, total_assets=100, long_term_debt=20, current_assets=40,
                current_liabilities=25, diluted_shares=110, gross_profit=35, revenue=90)
    score, n, tests = piotroski(cur, prev)
    assert n == 9 and score == 9
    prev2 = {k: None for k in prev}
    score, n, _ = piotroski(cur, prev2)
    assert score is None and n < 7


def test_altman_z_known_value():
    z = altman_z(working_capital=20, retained_earnings=30, ebit=10, market_cap=200, total_liabilities=50, revenue=150,
                 total_assets=100)
    expected = 1.2 * 0.2 + 1.4 * 0.3 + 3.3 * 0.1 + 0.6 * 4 + 1.0 * 1.5
    assert z == pytest.approx(expected)
    assert altman_z(None, 1, 1, 1, 1, 1, 1) is None


def _fd(company="TSTA"):
    from backend.data.normalisation import build_financials
    from backend.tests.fixtures import default_companies

    c = default_companies()[company]
    return c, build_financials(c.ticker, c.statements(), c.facts(), c.cik, "USD")


def test_calculator_on_fixture_company():
    c, fd = _fd()
    ms = FundamentalCalculator(fd).compute()
    yoy = latest_metric(ms, "revenue_yoy_annual")
    # synthetic quarterly growth 4% => annual growth = 1.04^4 - 1
    assert yoy.value == pytest.approx(1.04**4 - 1, rel=1e-6)
    om = latest_metric(ms, "operating_margin_ttm")
    assert om.value == pytest.approx(0.30, rel=1e-6)
    fcf = latest_metric(ms, "fcf_margin_ttm")
    # OCF = 0.8*0.3*1.15 = 0.276 of revenue, capex 6% => 0.216
    assert fcf.value == pytest.approx(0.276 - 0.06, rel=1e-6)
    de = latest_metric(ms, "debt_to_equity")
    assert de.value == pytest.approx(5e9 / 30e9)
    for m in ms:  # every metric documents its formula, and unavailable ones explain why
        assert m.formula
        if m.status != "ok":
            assert m.reason


def test_negative_equity_makes_ratios_not_meaningful():
    from backend.data.normalisation import build_financials
    from backend.tests.fixtures import Co

    c = Co("NEGQ", "Negative Equity (synthetic)", "Consumer Discretionary", "Restaurants", 1, 1e9, 0.01, 0.2, equity=-2e9)
    fd = build_financials(c.ticker, c.statements(), c.facts(), c.cik, "USD")
    ms = FundamentalCalculator(fd).compute()
    de = latest_metric(ms, "debt_to_equity")
    assert de.status == "not_meaningful" and de.value is None
    roe = latest_metric(ms, "roe_ttm")
    assert roe.status == "not_meaningful"


def test_quarterly_yoy_and_ttm():
    c, fd = _fd()
    ms = FundamentalCalculator(fd).compute()
    q = latest_metric(ms, "revenue_yoy_quarterly")
    assert q.value == pytest.approx(1.04**4 - 1, rel=1e-6)
    assert fd.ttm["revenue"].value == pytest.approx(sum(c.rev[20:24]))
    assert not math.isnan(fd.ttm["diluted_eps"].value)
