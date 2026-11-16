import { terms } from '../../i18n/zh-CN';
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
  const entries: { label: string; count: number; href: string }[] = [];
  if (tasks)
    entries.push({ label: terms.labels.home_tasks, count: tasks.active, href: '/work?status=active' });
  if (access.canEdit && returned !== null)
    entries.push({
      label: terms.labels.home_returned,
      count: returned,
      href: '/maintenance?tab=review&review_status=returned',
    });
  if (access.canReview && review !== null)
    entries.push({
      label: terms.labels.home_review,
      count: review,
      href: '/maintenance?tab=review&review_status=review',
    });
  if (access.canReview && relations !== null)
    entries.push({
      label: terms.labels.home_relations_review,
      count: relations,
      href: '/maintenance?tab=relations&relation_status=candidate',
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
export function parseRuns(responses: unknown[]): HomeRun[] {
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
  return [
    ...new Map(
      all
        .filter((item) => ['queued', 'running', 'retry', 'cancelling'].includes(item.status))
        .map((item) => [item.kind + ':' + item.id, item]),
    ).values(),
  ];
}
