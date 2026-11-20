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
  // committee & data quality
  await page.getByRole("tab", { name: "Investment committee" }).click();
  await expect(page.getByText("Devil's Advocate").first()).toBeVisible();
  await page.getByRole("tab", { name: "Data quality" }).click();
  await expect(page.getByText("Source retrievals")).toBeVisible();
  // ask (rule-based)
  await page.getByRole("tab", { name: "Ask" }).click();
  await page.getByRole("button", { name: "What is the strongest reason not to buy?" }).click();
  await expect(page.locator(".callout.pos")).toBeVisible();
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

test("screener lists the universe; mobile has no horizontal overflow", async ({ page }) => {
  await page.goto("/screener");
  await expect(page.getByText("TSTA").first()).toBeVisible();
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
