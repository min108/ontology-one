import { terms } from '../../i18n/zh-CN';
const copy = terms.labels;
// Only the business fields represented in the vocabulary belong in this view.
export const fieldLabels = Object.fromEntries(
  Object.entries(copy)
    .filter(([key]) => key.startsWith('r2_card_field_'))
    .map(([key, label]) => [key.slice('r2_card_field_'.length), label]),
);
const textFields = new Set(['name', 'label', 'title', 'value', 'text', 'description']);
export function businessText(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (Array.isArray(value)) return value.map(businessText).filter(Boolean).join('；');
  if (!value || typeof value !== 'object') return '';
  return Object.entries(value)
    .flatMap(([key, child]) => {
      const label = Object.hasOwn(fieldLabels, key) ? fieldLabels[key] : undefined;
      if (!label && !textFields.has(key)) return [];
      const text = businessText(child);
      return text ? [label ? `${label}：${text}` : text] : [];
    })
    .join('；');
}

export function sourceDescription(value: unknown): string {
  if (typeof value === 'string') return sourceLocator(value);
  if (!value || typeof value !== 'object') return '';
  const source = value as Record<string, unknown>;
  return [
    ...new Set(
      [
        businessText(source.title),
        sourceLocator(source.locator),
        businessText(source.quote) || businessText(source.excerpt),
      ].filter(Boolean),
    ),
  ].join(' · ');
}

// Character offsets and reading-version counters describe storage, not the cited policy.
// Preserve real clause/column labels, including those in older unanchored sources.
export function sourceLocator(value: unknown): string {
  return businessText(value)
    .split(/[·；]/)
    .filter((part) => !/原文第\s*\d+\s*[–—-]\s*\d+\s*字|阅读版\s*\d+/.test(part))
    .map((part) => part.trim())
    .filter(Boolean)
    .join(' · ');
}
