import type { Evidence, Item, Proposal } from './shared';
import { terms } from '../../i18n/zh-CN';
export const mainCopy = terms.labels;
export function sentence(template: string, values: Record<string, string | number>) {
  return template.replace(/\{(\w+)\}/g, (match, key: string) => String(values[key] ?? match));
}
export function isHandledElsewhere(item: Item) {
  return item.processed_elsewhere === true || item.decision === 'processed_elsewhere';
}
export function isSubmitted(item: Item) {
  return !!item.applied_entity_id && ['review', 'approved', 'published'].includes(item.applied_status || '');
}
export function readyForBulkAccept(item: Item) {
  return (
    item.decision === 'pending' &&
    !isHandledElsewhere(item) &&
    item.validation.level !== 'error' &&
    item.tier === 'sure' &&
    (item.ready_for_bulk_accept ?? item.gates?.passed ?? true)
  );
}
export function groupItems(items: Item[]) {
  const incomplete = (item: Item) => !!item.gates && !item.gates.passed;
  return (['incomplete', 'check', 'doubt', 'sure'] as const).map((tier) => ({
    tier,
    items: items.filter((item) =>
      tier === 'incomplete' ? incomplete(item) : !incomplete(item) && item.tier === tier,
    ),
  }));
}
export function changesetGate(proposal: Proposal, canEdit: boolean) {
  const active = proposal.items.filter((item) => !isHandledElsewhere(item));
  const acceptedItems = active.filter((item) => ['accepted', 'edited'].includes(item.decision));
  const ready = acceptedItems.filter(
    (item) =>
      !isSubmitted(item) &&
      (item.can_submit ?? item.gates?.ready_to_submit ?? item.gates?.passed ?? proposal.can_submit),
  );
  return {
    pending: active.filter((item) => item.decision === 'pending').length,
    accepted: acceptedItems.length,
    rejected: active.filter((item) => item.decision === 'rejected').length,
    ready: ready.length,
    remaining: acceptedItems.filter((item) => !isSubmitted(item) && !ready.includes(item)),
    submitted: acceptedItems.filter(isSubmitted).length,
    allowed: canEdit && proposal.can_submit && ready.length > 0,
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
