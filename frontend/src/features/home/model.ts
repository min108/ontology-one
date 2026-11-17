import { terms } from '../../i18n/zh-CN';
import type { Proposal } from '../../api/generated/types';
import { isHandledElsewhere } from '../build/model';
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error(terms.labels.home_unavailable);
  return value as Record<string, unknown>;
}
export function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0)
    throw new Error(terms.labels.home_unavailable);
  return value;
}
function counts(value: unknown): Record<string, number> {
  return Object.fromEntries(Object.entries(object(value)).map(([key, value]) => [key, number(value)]));
}
export function parseOverview(value: unknown) {
  const item = object(value),
    kinds = counts(item.counts);
  return {
    assets: number(item.assets),
    relations: number(item.relations),
    organizations: kinds.organization ?? 0,
    rows: number(item.data_rows),
    kinds,
    relationStatuses: counts(item.relation_statuses),
  };
}
export function parseTotal(value: unknown): number {
  return number(object(value).total);
}
export function taskCounts(value: unknown) {
  const list = object(value).tasks;
  if (!Array.isArray(list)) throw new Error(terms.labels.home_unavailable);
  const tasks = list.map((item) => {
    const status = object(item).status;
    if (typeof status !== 'string') throw new Error(terms.labels.home_unavailable);
    return status;
  });
  return {
    active: tasks.filter((status) => !['completed', 'published', 'archived'].includes(status)).length,
  };
}
export function todoEntries(
  access: { canEdit: boolean; canReview: boolean },
  tasks: { active: number } | null,
  review: number | null,
  returned: number | null,
  relations: number | null,
) {
  const entries: { label: string; count: number; href: string; step?: number }[] = [];
  if (tasks)
    entries.push({ label: terms.labels.home_tasks, count: tasks.active, href: '/work?status=active' });
  if (access.canEdit && returned !== null)
    entries.push({
      label: terms.labels.home_returned,
      count: returned,
      href: '/maintenance?tab=review&review_status=returned',
      step: 2,
    });
  if (access.canReview && review !== null)
    entries.push({
      label: terms.labels.home_review,
      count: review,
      href: '/maintenance?tab=review&review_status=review',
      step: 3,
    });
  if (access.canReview && relations !== null)
    entries.push({
      label: terms.labels.home_relations_review,
      count: relations,
      href: '/maintenance?tab=relations&relation_status=candidate',
      step: 3,
    });
  return entries;
}
export type HomeRun = {
  id: string;
  kind: string;
  status: string;
  title: string;
  href?: string;
  run_id?: string;
  entity_id?: string;
  session_id?: string;
};
export function parseRunHistory(responses: unknown[]): HomeRun[] {
  const all = responses.flatMap((response) => {
    const items = object(response).items;
    if (!Array.isArray(items)) throw new Error(terms.labels.home_unavailable);
    return items.map((item) => {
      const value = object(item);
      for (const key of ['id', 'kind', 'status'])
        if (typeof value[key] !== 'string') throw new Error(terms.labels.home_unavailable);
      const row: HomeRun = {
        id: String(value.id),
        kind: String(value.kind),
        status: String(value.status),
        title: typeof value.title === 'string' ? value.title : String(value.id),
      };
      for (const key of ['href', 'run_id', 'entity_id', 'session_id'] as const)
        if (typeof value[key] === 'string') row[key] = value[key];
      return row;
    });
  });
  return [...new Map(all.map((item) => [item.kind + ':' + item.id, item])).values()];
}
export function parseRuns(responses: unknown[]): HomeRun[] {
  return parseRunHistory(responses).filter((item) =>
    ['queued', 'running', 'retry', 'cancelling'].includes(item.status),
  );
}
export function recipeChangesetId(run: HomeRun): string | undefined {
  return run.kind === 'recipe_run' && run.status === 'completed'
    ? /^\/build\/changesets\/([a-zA-Z0-9_-]+)$/.exec(run.href || '')?.[1]
    : undefined;
}
export function proposalTodo(proposal: Proposal) {
  if (['submitted', 'archived'].includes(proposal.status)) return undefined;
  const pending = proposal.items.filter(
    (item) => item.decision === 'pending' && !item.applied_entity_id && !isHandledElsewhere(item),
  ).length;
  const accepted = proposal.can_submit
    ? proposal.items.filter(
        (item) => ['accepted', 'edited'].includes(item.decision) && !isHandledElsewhere(item),
      ).length
    : 0;
  const count = pending || accepted;
  if (proposal.status === 'applied' && !proposal.can_submit) return undefined;
  return count
    ? {
        label: proposal.title,
        count,
        href: '/build/changesets/' + encodeURIComponent(proposal.id),
        step: 2,
        proposal: pending ? 'check' : 'submit',
      }
    : undefined;
}
export function recipeProgressHref(run: HomeRun): string | undefined {
  return run.kind === 'recipe_run' &&
    ['queued', 'running', 'retry', 'cancelling'].includes(run.status) &&
    /^\/build\/intake\?run=[a-zA-Z0-9_-]+$/.test(run.href || '')
    ? run.href
    : undefined;
}
