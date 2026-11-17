import type { Evidence, Item, Proposal } from './shared';
import { terms } from '../../i18n/zh-CN';
export const mainCopy = terms.labels;
export function sentence(template: string, values: Record<string, string | number>) {
  return template.replace(/\{(\w+)\}/g, (match, key: string) => String(values[key] ?? match));
}
export function isHandledElsewhere(item: Item) {
  return item.processed_elsewhere === true || item.decision === 'processed_elsewhere';
}
export function groupItems(items: Item[]) {
  return (['check', 'doubt', 'sure'] as const).map((tier) => ({
    tier,
    items: items.filter((item) => item.tier === tier),
  }));
}
export function changesetGate(proposal: Proposal, canEdit: boolean) {
  const pending = proposal.items.filter(
    (item) => !isHandledElsewhere(item) && item.decision === 'pending',
  ).length;
  const accepted = proposal.items.filter(
    (item) => !isHandledElsewhere(item) && ['accepted', 'edited'].includes(item.decision),
  ).length;
  const rejected = proposal.items.filter(
    (item) => !isHandledElsewhere(item) && item.decision === 'rejected',
  ).length;
  return {
    pending,
    accepted,
    rejected,
    allowed: canEdit && proposal.can_submit && pending === 0 && accepted > 0,
  };
}
export const askHref = (id: string) => '/applications?' + new URLSearchParams({ element: id });

/** Legacy sources can describe datasets or imported excerpts without a material anchor. */
export function isAnchoredEvidence(value: unknown): value is Evidence {
  if (!value || typeof value !== 'object') return false;
  const fields = value as Record<string, unknown>;
  return ['material_id', 'anchor_id', 'source_version', 'quote'].every(
    (key) => typeof fields[key] === 'string' && fields[key].trim().length > 0,
  );
}
