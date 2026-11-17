import { Descriptions, Tabs } from 'antd';
import { Link, useParams } from 'react-router-dom';
import { terms } from '../../i18n/zh-CN';
import { useUiStore } from '../../state/ui';
import { PageHeader } from '../../ui/PageHeader';
import { StatusTag } from '../../ui/StatusTag';
import { ResourceState, useResource } from '../admin/shared';
import { sourceHref, type Evidence, useBuildIdentity } from '../build/shared';
import styles from '../build/Build.module.css';

const copy = terms.labels;
// Only the business fields represented in the vocabulary belong in this view.
const fieldLabels = Object.fromEntries(
  Object.entries(copy)
    .filter(([key]) => key.startsWith('r2_card_field_'))
    .map(([key, label]) => [key.slice('r2_card_field_'.length), label]),
);
const textFields = new Set(['name', 'label', 'title', 'value', 'text', 'description']);
function businessText(value: unknown): string {
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
const activityLabels: Record<string, string> = {
  create: terms.actions.catalog_2_0,
  update: terms.actions.catalog_1_1,
  edit: terms.actions.catalog_1_1,
  save: terms.actions.catalog_2_0,
  submit: terms.actions.submit,
  withdraw: terms.actions.catalog_2_2,
  approve: terms.actions.catalog_2_3,
  publish: terms.actions.catalog_2_4,
  return: terms.actions.catalog_2_6,
  revise: terms.actions.catalog_2_8,
  archive: terms.actions.catalog_2_9,
  restore: terms.actions.catalog_2_10,
  complete: terms.actions.catalog_3_4,
};
function activityLabel(action: string): string {
  if (Object.hasOwn(activityLabels, action)) return activityLabels[action];
  // Older records already carry a business-facing Chinese action name.
  return /^[\p{Script=Han}\s、，。（）]+$/u.test(action) ? action : '';
}
function beijingTime(value: string): string {
  // Older database timestamps without an offset also represent UTC.
  const normalized = value.trim().replace(' ', 'T');
  const date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized) ? normalized : normalized + 'Z');
  if (Number.isNaN(date.getTime())) return copy.identity_not_recorded;
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value;
  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}`;
}
export interface ElementDetail {
  entity: {
    id: string;
    kind: string;
    title: string;
    status: string;
    version: number;
    domain: string;
    payload: Record<string, unknown>;
    activities: { id: string; action: string; detail: string; created_at: string }[];
  };
  card: { one_liner: string; points: string[]; relations: string[]; usage: string };
  versions: { id: string; version: number; created_at: string; reason?: string; change_note?: string }[];
  can_edit: boolean;
}
export function BusinessCard({ detail }: { detail: ElementDetail }) {
  const sources = Array.isArray(detail.entity.payload.source_refs)
    ? (detail.entity.payload.source_refs as Evidence[])
    : [];
  return (
    <article className={styles.card} aria-label={detail.entity.title}>
      <p className={styles.paragraph}>{detail.card.one_liner}</p>
      <h2>{copy.r2_card_points}</h2>
      <ul>
        {detail.card.points.map((point, i) => (
          <li key={i}>{point}</li>
        ))}
      </ul>
      <h2>{copy.r2_card_sources}</h2>
      {!sources.length && <p>{copy.r2_card_empty}</p>}
      {sources.map((source, i) => (
        <blockquote className={styles.quote} key={i}>
          <p>{source.quote}</p>
          {source.material_id && source.anchor_id ? (
            <Link to={sourceHref(source)}>{source.locator || copy.r2_source_open}</Link>
          ) : (
            <span>{source.locator}</span>
          )}
        </blockquote>
      ))}
      <h2>{copy.r2_card_usage}</h2>
      <p>{detail.card.usage}</p>
    </article>
  );
}
export default function ElementPage() {
  const { id = '' } = useParams(),
    identity = useBuildIdentity();
  return <Element key={identity.key + id} id={id} />;
}
function Element({ id }: { id: string }) {
  const viewMode = useUiStore((state) => state.viewMode);
  const query = useResource<{ data: ElementDetail }>('/v2/elements/' + encodeURIComponent(id));
  if (!query.data) return <ResourceState error={query.error} reload={query.reload} />;
  const detail = query.data.data,
    entity = detail.entity;
  const labels: Record<string, string> = terms.kinds;
  const fields = Object.entries(fieldLabels).flatMap(([key, label]) => {
    const text = businessText(entity.payload[key]);
    return text ? [{ key, label, children: <span className={styles.paragraph}>{text}</span> }] : [];
  });
  const activities = (entity.activities || []).flatMap((event) => {
    const label = activityLabel(event.action);
    return label ? [{ ...event, label }] : [];
  });
  return (
    <div className={styles.page}>
      <Link to="/ontology/catalog">{terms.actions.r2_card_back}</Link>
      <PageHeader title={entity.title} status={<StatusTag status={entity.status} />} />
      <p>
        {labels[entity.kind] || copy.unknown_kind}
        {viewMode === 'professional' && <> · {entity.id}</>} · v{entity.version} · {entity.domain}
      </p>
      <BusinessCard detail={detail} />
      {detail.can_edit && <Link to={'/assets/' + encodeURIComponent(id)}>{terms.actions.r2_card_edit}</Link>}
      <Tabs
        items={[
          {
            key: 'structure',
            label: copy.r2_card_structure,
            children: fields.length ? (
              <Descriptions column={1} items={fields} />
            ) : (
              <p>{copy.r2_card_empty}</p>
            ),
          },
          {
            key: 'relations',
            label: copy.r2_card_relations,
            children: detail.card.relations.length ? (
              <ul>
                {detail.card.relations.map((sentence, i) => (
                  <li key={i}>{sentence}</li>
                ))}
              </ul>
            ) : (
              <p>{copy.r2_card_empty}</p>
            ),
          },
          {
            key: 'versions',
            label: copy.r2_card_versions,
            children: detail.versions.length ? (
              <ul>
                {detail.versions.map((version) => (
                  <li key={version.id}>
                    v{version.version} · {beijingTime(version.created_at)} ·{' '}
                    {version.reason || version.change_note}
                  </li>
                ))}
              </ul>
            ) : (
              <p>{copy.r2_card_empty}</p>
            ),
          },
          {
            key: 'activity',
            label: copy.r2_card_activity,
            children: activities.length ? (
              <ul>
                {activities.map((event) => (
                  <li key={event.id}>
                    {event.label} · {event.detail} · {beijingTime(event.created_at)}
                  </li>
                ))}
              </ul>
            ) : (
              <p>{copy.r2_card_empty}</p>
            ),
          },
        ]}
      />
    </div>
  );
}
