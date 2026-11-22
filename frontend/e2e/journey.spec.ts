import { expect, test } from "@playwright/test";

test("search, confirm, run, inspect evidence, ask", async ({ page }) => {
  await page.goto("/analyze");
  await page.getByLabel("Search company").fill("testa");
  await page.getByRole("button", { name: /TSTA/ }).click();
  await expect(page.getByText("Testa Systems (synthetic)").first()).toBeVisible();
  await page.getByRole("button", { name: /Confirm TSTA and start analysis/ }).click();
  await expect(page).toHaveURL(/analysis\//);
  await expect(page.locator(".decision-head")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Rule-based mode/)).toBeVisible();
  // rulebook verdict: four pillars with keyboard-accessible drill-down into the measures
  await expect(page.getByRole("heading", { name: "Rulebook verdict" })).toBeVisible();
  for (const p of ["Quality", "Value", "Momentum", "Revisions"]) await expect(page.getByRole("meter", { name: new RegExp(`^${p} \\d+ of 100`) })).toBeVisible();
  const quality = page.locator(".pillar-head").filter({ hasText: "Quality" });
  await quality.focus();
  await page.keyboard.press("Enter");
  await expect(quality).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#pm-quality").getByText(/ROIC, 5-year average/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "What would change the call" })).toBeVisible();
  // valuation analyst tab
  await page.getByRole("tab", { name: "Valuation" }).click();
  await expect(page.getByText("What the price implies")).toBeVisible();
  await page.getByRole("tab", { name: "Decision" }).click();
  // research log shows real backend events
  await page.getByText(/Research log/).click();
  await expect(page.getByText("Analysis complete", { exact: false })).toBeVisible();
  // evidence drawer
  await page.getByRole("tab", { name: "Fundamentals" }).click();
  await page.locator(".chip").first().click();
  await expect(page.getByRole("dialog", { name: "Evidence" })).toBeVisible();
  await expect(page.getByText(/Formula|Source/).first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // grounded question about a specific metric
  await page.locator("table tbody tr").filter({ hasText: "Operating margin (TTM)" }).first().click();
  await page.getByRole("button", { name: "Ask about this metric" }).first().click();
  await expect(page.getByRole("tab", { name: "Ask" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".callout.pos").first()).toBeVisible();
  // committee & data quality
  await page.getByRole("tab", { name: "Investment committee" }).click();
  await expect(page.getByText("Devil's Advocate").first()).toBeVisible();
  await page.getByRole("tab", { name: "Data quality" }).click();
  await expect(page.getByText("Source retrievals")).toBeVisible();
  // ask (rule-based)
  await page.getByRole("tab", { name: "Ask" }).click();
  await page.getByLabel("Question").fill("What is the strongest reason not to buy?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.locator(".callout.pos")).toHaveCount(2);
});

test("no autoplay; briefing transcript visible; stop available", async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { __spoken: number }).__spoken = 0;
    const orig = window.speechSynthesis?.speak?.bind(window.speechSynthesis);
    if (window.speechSynthesis) window.speechSynthesis.speak = (u) => { (window as unknown as { __spoken: number }).__spoken++; orig?.(u); };
  });
  await page.goto("/history");
  await page.locator("a.mono, a strong.mono").first().click().catch(() => {});
  if (!/analysis\//.test(page.url())) {
    await page.goto("/analyze?ticker=TSTB");
    await page.getByRole("button", { name: /Confirm TSTB/ }).click();
  }
  await expect(page.getByText("Listen to briefing").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByLabel("Briefing transcript")).toContainText("Recommendation.");
  expect(await page.evaluate(() => (window as unknown as { __spoken: number }).__spoken)).toBe(0);
  await expect(page.getByRole("button", { name: "Stop audio" })).toBeVisible();
});

test("microphone denial falls back to typing", async ({ page, context }) => {
  await context.clearPermissions();
  await page.addInitScript(() => {
    class FakeRec {
      onerror: ((e: { error: string }) => void) | null = null;
      onend: (() => void) | null = null;
      start() { setTimeout(() => { this.onerror?.({ error: "not-allowed" }); this.onend?.(); }, 50); }
      stop() {}
    }
    (window as unknown as Record<string, unknown>).webkitSpeechRecognition = FakeRec;
    (window as unknown as Record<string, unknown>).SpeechRecognition = FakeRec;
  });
  await page.goto("/analyze?ticker=TSTC");
  await page.getByRole("button", { name: /Confirm TSTC/ }).click();
  await expect(page.locator(".decision-head")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("tab", { name: "Ask" }).click();
  await page.getByRole("button", { name: "Ask by voice" }).click();
  await expect(page.getByText(/Microphone permission was denied/)).toBeVisible();
  await page.getByLabel("Question").fill("Explain the valuation in simple terms.");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.locator(".callout.pos").first()).toBeVisible();
});

test("screener classifies the universe with drill-down; mobile has no horizontal overflow", async ({ page }) => {
  await page.goto("/screener");
  await expect(page.getByText("TSTA").first()).toBeVisible();
  await page.getByRole("button", { name: "Refresh universe" }).click();
  const firstLabel = page.locator("tbody tr .badge.rec").first();
  await expect(firstLabel).toBeVisible({ timeout: 60_000 });
  await page.reload();
  // label distribution segments filter the table
  const chip = page.locator(".chip").filter({ hasText: /Watchlist|Reject|Wait|Buy/ }).first();
  const chipLabel = (await chip.innerText()).replace(/\s*\d+$/, "").trim();
  await chip.click();
  await expect(page.locator("tbody tr .badge.rec").first()).toHaveText(chipLabel);
  // row drill-down shows the pillar measures and the decision trace
  await page.locator("tbody tr.clickable").first().click();
  await expect(page.getByRole("link", { name: "Run full analysis" })).toBeVisible();
  await expect(page.locator(".drawer-row .pillar-head").first()).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("keyboard navigation reaches tabs with visible focus", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);
  expect(outline).not.toBe("none");
});

test("voice turned off hides audio controls but keeps transcripts", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("ii_voice", JSON.stringify({ enabled: false })));
  await page.goto("/analyze?ticker=TSTD");
  await page.getByRole("button", { name: /Confirm TSTD/ }).click();
  await expect(page.locator(".decision-head")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Voice is turned off in Settings/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Listen to briefing" })).toHaveCount(0);
  await page.getByRole("tab", { name: "Ask" }).click();
  await expect(page.getByText(/Voice input is off/)).toBeVisible();
});
