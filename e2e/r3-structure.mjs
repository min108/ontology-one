import { appendFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect } from "./runtime.mjs";

// UI-BASELINE §11 / benchmarks/check-baseline.js. The three viewport measurements
// preserve the benchmark's 1440 character budget while covering R3's 1366/1920 gate.
const SMALL_OK = [
  ".zy-caption",
  ".zy-tag",
  ".zy-label",
  ".zy-kbd",
  ".zy-count",
  ".zy-pick__meta",
  ".zy-pick__state",
  ".zy-group",
  ".zy-step__n",
  ".zy-head__back",
  ".zy-avatar",
  ".zy-seg small",
  ".zy-nav--collapsed",
  ".zy-nav__item",
  ".bd-cap",
  "[data-meta]",
  "time",
  "small",
  ".ant-tag",
  ".ant-avatar",
  "[data-page-header] nav",
  "[class*='caption']",
  "[class*='Caption']",
  "[class*='meta']",
  "[class*='Meta']",
  "[class*='label']",
  "[class*='Label']",
  "[class*='count']",
  "[class*='Count']",
  "[class*='stepNumber']",
  "nav[aria-label='下一步'] li > span:first-child",
  "[data-primary-link]",
  "nav[aria-label='工作区内导航']",
].join(",");
const budgets = { workbench: 400, archive: 500, split: 650, list: 800 };
const leakPatterns = [
  ["object number", /\b[A-Z]{2,}-\d+[\w-]*\b/g],
  ["32 digit identifier", /\b[a-f\d]{32}\b/gi],
  ["internal field", /\b[a-z]+(?:_[a-z]+)+\b/g],
  ["JSON", /\{\s*"[^"\n]+"\s*:/g],
  ["raw time", /\d{4}-\d\d-\d\dT\d\d:\d\d[^\s]*|\bUTC\b/g],
  [
    "raw state",
    /\b(?:pending|published|returned|rejected|accepted|queued|running|completed|failed|waiting_user|provision_failed|draft|review)\b/g,
  ],
];
export function leakedText(text) {
  return leakPatterns.flatMap(([kind, pattern]) =>
    [...text.matchAll(pattern)].map((match) => ({ kind, value: match[0] })),
  );
}
export async function assertNoLeaks(page, label) {
  const text = await page.locator("body").innerText();
  expect.soft(leakedText(text), `${label}: G3 simple view leakage`).toEqual([]);
}
export async function checkStructure(
  page,
  label,
  { legacy = false, empty = false } = {},
) {
  const original = page.viewportSize();
  for (const [width, height] of [
    [1366, 768],
    [1440, 900],
    [1920, 1080],
  ]) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => window.scrollTo(0, 0));
    const r = await page.evaluate(
      ({ SMALL_OK, legacy }) => {
        const shown = (el) =>
          !!el.getClientRects().length &&
          getComputedStyle(el).visibility !== "hidden" &&
          getComputedStyle(el).display !== "none" &&
          !el.closest("[aria-hidden='true']");
        const inside = (el) => {
          const box = el.getBoundingClientRect();
          return (
            shown(el) &&
            box.bottom > 0 &&
            box.top < innerHeight &&
            box.right > 0 &&
            box.left < innerWidth
          );
        };
        const shellMain = document.querySelector("main");
        const layout = document.querySelector("[data-page-template]");
        const content =
          layout?.querySelector("[data-page-content]") ||
          document.querySelector(".legacy-root");
        const title =
          layout?.querySelector("[data-page-title]") ||
          document.querySelector(".legacy-root h1");
        const nav =
          document.querySelector("nav[aria-label='工作区导航']") ||
          document.querySelector("[data-primary-link]")?.closest("nav");
        const firstBlock = content && [...content.children].find(shown);
        const textNodes = [];
        let first = 0,
          all = 0;
        const walker = document.createTreeWalker(
          document.body,
          NodeFilter.SHOW_TEXT,
        );
        while (walker.nextNode()) {
          const node = walker.currentNode,
            el = node.parentElement,
            text = node.textContent.replace(/\s+/g, "");
          if (!text || !el || el.closest("script,style,svg,[data-meta]"))
            continue;
          const folded =
            el.closest("details:not([open])") && !el.closest("summary");
          const visible = shown(el) && !folded;
          const layered =
            !visible &&
            (el.closest("details:not([open])") || el.closest(".ant-drawer"));
          if (
            content?.contains(el) &&
            (visible || layered) &&
            !el.closest("[hidden]")
          )
            all += text.length;
          if (!visible) continue;
          const size = parseFloat(getComputedStyle(el).fontSize);
          textNodes.push({
            size,
            secondary: !!el.closest(SMALL_OK),
            text: text.slice(0, 30),
          });
          if (content?.contains(el) && !el.closest(".ant-drawer")) {
            const range = document.createRange();
            range.selectNodeContents(node);
            const box = range.getBoundingClientRect();
            if (
              box.top < innerHeight &&
              box.bottom > 0 &&
              box.left < innerWidth
            )
              first += text.length;
          }
        }
        const probe = document.createElement("i");
        probe.style.color =
          getComputedStyle(document.documentElement)
            .getPropertyValue("--zy-brand-500")
            .trim() || "#a51c30";
        document.body.append(probe);
        const accent = getComputedStyle(probe).color;
        probe.remove();
        const accentElements = [...document.querySelectorAll("body *")].filter(
          (el) => {
            if (
              !inside(el) ||
              el.closest(
                "[data-brand], [class*='brand'], [class*='Brand'], [data-meta]",
              )
            )
              return false;
            const cs = getComputedStyle(el),
              before = getComputedStyle(el, "::before");
            return (
              cs.backgroundColor === accent ||
              (before.content !== "none" &&
                before.backgroundColor === accent) ||
              el.matches("[data-needs-confirmation]")
            );
          },
        );
        const tables = [...document.querySelectorAll("table")].filter(shown);
        const columns = Math.max(
          0,
          ...tables.flatMap((t) =>
            [...t.querySelectorAll("thead tr")].map(
              (r) => [...r.children].filter(shown).length,
            ),
          ),
        );
        const rowButtons = Math.max(
          0,
          ...tables.flatMap((t) =>
            [...t.querySelectorAll("tbody tr")].map(
              (r) => [...r.querySelectorAll("button")].filter(shown).length,
            ),
          ),
        );
        const firstRow = tables
          .flatMap((t) => [
            ...t.querySelectorAll("tbody tr:not(.ant-table-placeholder)"),
          ])
          .find(shown);
        const pad = layout
          ? parseFloat(getComputedStyle(layout).paddingLeft)
          : 0;
        const round = (value) =>
          value === undefined ? null : Math.round(value);
        const titleX = round(title?.getBoundingClientRect().left);
        const expectedX = round(
          (nav?.getBoundingClientRect().right ??
            shellMain?.getBoundingClientRect().left) + pad,
        );
        return {
          template: layout?.dataset.pageTemplate,
          minSize: Math.min(...textNodes.map((n) => n.size)),
          sizes: [...new Set(textNodes.map((n) => n.size))].sort(
            (a, b) => a - b,
          ),
          smallBad: textNodes.filter((n) => n.size < 14 && !n.secondary),
          titleX,
          expectedX,
          blockX: round(firstBlock?.getBoundingClientRect().left),
          bodyY: round(content?.getBoundingClientRect().top),
          headers: layout?.querySelectorAll(":scope > [data-page-header]")
            .length,
          extraHorizontalBars: [...(shellMain?.children || [])]
            .filter(
              (el) =>
                shown(el) &&
                el !== layout &&
                el.getBoundingClientRect().bottom <=
                  (content?.getBoundingClientRect().top || 0),
            )
            .map((el) => el.tagName),
          rowY: round(firstRow?.getBoundingClientRect().top),
          overflow:
            document.documentElement.scrollWidth > innerWidth ||
            (content && content.scrollWidth > content.clientWidth + 1),
          first,
          all,
          accents: accentElements.length,
          primaries: [
            ...document.querySelectorAll(
              ".ant-btn-primary:not([disabled]),.zy-btn--primary:not([disabled])",
            ),
          ].filter(inside).length,
          columns,
          rowButtons,
          bareTags: [...document.querySelectorAll(".ant-tag")]
            .filter(
              (el) =>
                shown(el) &&
                !(el.querySelector("svg") && el.textContent.trim()),
            )
            .map((el) => el.textContent),
          nativeDates: document.querySelectorAll(
            "input[type=date],input[type=datetime-local]",
          ).length,
          zeroMetrics: [
            ...document.querySelectorAll(
              ".ant-statistic-content,[data-metric-value]",
            ),
          ].filter((el) => shown(el) && el.textContent.trim() === "0").length,
          visibleText: document.body.innerText,
        };
      },
      { SMALL_OK, legacy },
    );
    appendFileSync(
      resolve(process.env.E2E_EVIDENCE_DIR, "structure.jsonl"),
      JSON.stringify({ label, width, height, ...r }) + "\n",
    );
    const name = `${label} ${width}×${height}`;
    expect.soft(r.minSize, `${name} A1`).toBeGreaterThanOrEqual(12);
    expect.soft(r.smallBad, `${name} A2`).toEqual([]);
    expect.soft(r.overflow, `${name} C1`).toBeFalsy();
    if (!legacy) {
      expect
        .soft(r.sizes.length, `${name} A3 size count`)
        .toBeLessThanOrEqual(5);
      expect
        .soft(
          r.sizes.filter((s) => ![12, 14, 16, 20, 36].includes(s)),
          `${name} A3 scale`,
        )
        .toEqual([]);
      expect.soft(r.titleX, `${name} B1 title`).toBe(r.expectedX);
      expect.soft(r.blockX, `${name} B1 content`).toBe(r.titleX);
      expect.soft(r.headers, `${name} B2 one title area`).toBe(1);
      expect
        .soft(r.extraHorizontalBars, `${name} B2 no additional bars`)
        .toEqual([]);
      if (width === 1366) {
        expect
          .soft(r.bodyY, `${name} B2`)
          .toBeLessThanOrEqual(r.template === "workbench" ? 256 : 196);
        if (r.template === "list" && r.rowY !== null)
          expect.soft(r.rowY, `${name} B3`).toBeLessThanOrEqual(264);
      }
      if (width === 1440) {
        expect
          .soft(r.first, `${name} D1`)
          .toBeLessThanOrEqual(budgets[r.template]);
        if (["workbench", "archive"].includes(r.template) && r.all >= 600)
          expect.soft(r.first / r.all, `${name} D2`).toBeLessThanOrEqual(0.5);
      }
      expect.soft(r.accents, `${name} E1`).toBeLessThanOrEqual(3);
      expect.soft(r.primaries, `${name} E2`).toBeLessThanOrEqual(1);
      expect.soft(r.columns, `${name} G1 columns`).toBeLessThanOrEqual(6);
      expect.soft(r.rowButtons, `${name} G1 actions`).toBeLessThanOrEqual(2);
      expect.soft(r.bareTags, `${name} G2`).toEqual([]);
      expect.soft(leakedText(r.visibleText), `${name} G3/G5`).toEqual([]);
      expect.soft(r.nativeDates, `${name} G5 date controls`).toBe(0);
      if (empty) {
        expect.soft(r.zeroMetrics, `${name} G4`).toBe(0);
        expect.soft(r.primaries, `${name} G4 one start action`).toBe(1);
      }
    }
    if (width === 1366 && process.env.E2E_R3_LIVE !== "1")
      await page.screenshot({
        path: resolve(
          process.env.E2E_EVIDENCE_DIR,
          `${label.replace(/[^\w-]/g, "-")}.png`,
        ),
        animations: "disabled",
      });
  }
  await page.setViewportSize(original);
}
