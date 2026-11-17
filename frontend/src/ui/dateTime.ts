import { terms } from '../i18n/zh-CN';
export function formatDateTime(value: string | null | undefined, relative = false, now = new Date()): string {
  if (!value) return '—';
  // Naive service timestamps are UTC, consistent with the storage contract.
  const normalized = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(value)
    ? value.replace(' ', 'T') + 'Z'
    : value;
  const date = new Date(normalized);
  if (!Number.isFinite(date.getTime())) return '—';
  const parts = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date);
  if (!relative) return parts;
  const today = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now);
  const yesterday = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(now.getTime() - 86400000));
  return parts.startsWith(today)
    ? `${terms.labels.r3_ui_today} ${parts.slice(11)}`
    : parts.startsWith(yesterday)
      ? `${terms.labels.r3_ui_yesterday} ${parts.slice(11)}`
      : parts;
}
