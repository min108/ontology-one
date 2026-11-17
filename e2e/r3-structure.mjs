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
  "[data-origin]",
  "[data-split-list] h3",
  "[data-split-list] button > span",
  "time",
  "small",
  "sup",
  ".ant-tag",
  ".legacy-root .badge",
  ".legacy-root .stat > span",
  ".legacy-root .guidance-note",
  ".legacy-root .small",
  ".legacy-root .page-head .biz-text-link",
  ".ant-avatar",
  "[data-page-header] nav",
  "[data-object-return]",
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
const live = (process.env.E2E_R3_LIVE || process.env.E2E_R2_LIVE) === "1";
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
export async function assertNoLeaks(
  page,
  label,
  { professional = false } = {},
) {
  const text = await page.evaluate(
    ({ professional }) => {
      const values = [],
        walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode,
          el = node.parentElement;
        if (
          !el ||
          el.closest("script,style,svg,[aria-hidden=true]") ||
          !el.getClientRects().length ||
          getComputedStyle(el).visibility === "hidden"
        )
          continue;
        if (el.closest("details:not([open])") && !el.closest("summary"))
          continue;
        if (professional && el.closest("[data-technical-details]")) continue;
        values.push(node.textContent);
      }
      return values.join("\n");
    },
    { professional },
  );
  const leaks = leakedText(text);
  if (live && leaks.length && process.env.E2E_EVIDENCE_DIR) {
    const counts = {};
    for (const { kind } of leaks) counts[kind] = (counts[kind] || 0) + 1;
    // Only static check labels and enumerated kinds survive the live run.
    // Never retain the matching value or its surrounding model text.
    appendFileSync(
      resolve(process.env.E2E_EVIDENCE_DIR, "leak-categories.jsonl"),
      JSON.stringify({ label, counts }) + "\n",
    );
  }
  expect
    .soft(
      live ? leaks.map(({ kind }) => ({ kind })) : leaks,
      `${label}: G3 visible text leakage`,
    )
    .toEqual([]);
}
function violationCodes(r, width, { legacy, empty }) {
  const codes = [];
  if (r.minSize < 12) codes.push("A1");
  if (r.smallBad.length) codes.push("A2");
  if (r.titleX !== r.expectedX || r.blockX !== r.titleX) codes.push("B1");
  if (r.overflow) codes.push("C1");
  if (!legacy) {
    if (
      r.sizes.length > 5 ||
      r.sizes.some((size) => ![12, 14, 16, 20, 36].includes(size))
    )
      codes.push("A3");
    if (
      r.headers !== 1 ||
      r.extraHorizontalBars.length ||
      (width === 1366 && r.bodyY > (r.template === "workbench" ? 256 : 196))
    )
      codes.push("B2");
    if (
      width === 1366 &&
      r.template === "list" &&
      r.rowY !== null &&
      r.rowY > 264
    )
      codes.push("B3");
    if (width === 1440 && r.first > budgets[r.template]) codes.push("D1");
    if (
      width === 1440 &&
      ["workbench", "archive"].includes(r.template) &&
      r.all >= 600 &&
      r.first / r.all > 0.5
    )
      codes.push("D2");
    if (r.accents > 3) codes.push("E1");
    if (r.primaries > 1) codes.push("E2");
    if (r.columns > 6 || r.rowButtons > 2) codes.push("G1");
    if (r.bareTags.length) codes.push("G2");
    if (leakedText(r.visibleText).length) codes.push("G3/G5");
    if (empty && (r.zeroMetrics || r.primaries !== 1)) codes.push("G4");
    if (r.nativeDates) codes.push("G5");
  }
  return codes;
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
          document.querySelector("[data-legacy-page] .page");
        const title =
          layout?.querySelector("[data-page-title]") ||
          document.querySelector("[data-legacy-page] h1");
        const nav =
          document.querySelector("nav[aria-label='工作区导航']") ||
          document.querySelector("[data-primary-link]")?.closest("nav");
        const firstBlock =
          content &&
          [...content.children].find(
            (el) =>
              shown(el) &&
              (!legacy ||
                (!el.contains(title) &&
                  !el.matches("nav,.page-head,.agent-page-heading"))),
          );
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
            className: el.className,
            parentClass: el.parentElement?.className,
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
        const tables = [...(content?.querySelectorAll("table") || [])].filter(
          shown,
        );
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
        const pad =
          layout || content
            ? parseFloat(getComputedStyle(layout || content).paddingLeft)
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
    const { visibleText, smallBad, bareTags, ...measurements } = r;
    appendFileSync(
      resolve(process.env.E2E_EVIDENCE_DIR, "structure.jsonl"),
      JSON.stringify({
        label,
        width,
        height,
        ...measurements,
        violations: violationCodes(r, width, { legacy, empty }),
        bareTags: live ? bareTags.length : bareTags,
        smallBad: live
          ? smallBad.map(({ size, secondary }) => ({ size, secondary }))
          : smallBad,
        ...(!live ? { visibleText } : {}),
      }) + "\n",
    );
    const name = `${label} ${width}×${height}`;
    expect.soft(r.minSize, `${name} A1`).toBeGreaterThanOrEqual(12);
    expect
      .soft(
        r.smallBad.map(({ size, secondary }) => ({ size, secondary })),
        `${name} A2`,
      )
      .toEqual([]);
    expect.soft(r.overflow, `${name} C1`).toBeFalsy();
    expect.soft(r.titleX, `${name} B1 title`).toBe(r.expectedX);
    expect.soft(r.blockX, `${name} B1 content`).toBe(r.titleX);
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
      expect.soft(r.bareTags.length, `${name} G2`).toBe(0);
      expect
        .soft(
          live
            ? leakedText(r.visibleText).map(({ kind }) => ({ kind }))
            : leakedText(r.visibleText),
          `${name} G3/G5`,
        )
        .toEqual([]);
      expect.soft(r.nativeDates, `${name} G5 date controls`).toBe(0);
      if (empty) {
        expect.soft(r.zeroMetrics, `${name} G4`).toBe(0);
        expect.soft(r.primaries, `${name} G4 one start action`).toBe(1);
      }
    }
    if (width === 1366 && !live)
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
