import { Button, Descriptions } from 'antd';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { terms } from '../../i18n/zh-CN';
import { useUiStore } from '../../state/ui';
import {
  PageHeader,
  PageLayout,
  FoldRow,
  TechnicalDetails,
  KindTag,
  StatusTag,
  formatDateTime,
} from '../../ui';
import { SourceEvidence } from '../build/SourceEvidence';
import { askHref, sentence } from '../build/model';
import { ResourceState, useResource } from '../admin/shared';
import { type Evidence, useBuildIdentity } from '../build/shared';
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
  const [source, setSource] = useState<Evidence>();
  const sources = Array.isArray(detail.entity.payload.source_refs)
    ? (detail.entity.payload.source_refs as Evidence[])
    : [];
  return (
    <article className={styles.card} aria-label={detail.entity.title}>
      <p className={styles.statement}>{detail.card.one_liner}</p>
      {detail.card.points.length > 0 && (
        <>
          <h2>{copy.r2_card_points}</h2>
          <ul>
            {detail.card.points.slice(0, 3).map((point, i) => (
              <li key={i}>{point}</li>
            ))}
          </ul>
        </>
      )}
      <h2>{copy.r2_card_sources}</h2>
      {!sources.length && <p>{copy.r2_card_empty}</p>}
      {sources.slice(0, 1).map((ref, i) => (
        <blockquote className={styles.quote} key={i}>
          <p>{ref.quote}</p>
          <Button type="text" autoInsertSpace={false} onClick={() => setSource(ref)}>
            {ref.locator || copy.r3_main_view_original}
          </Button>
        </blockquote>
      ))}
      {sources.length > 1 && (
        <FoldRow
          title={copy.r3_main_source}
          summary={sentence(copy.r3_main_count, { count: sources.length })}
        >
          {sources.slice(1).map((ref, i) => (
            <p key={i}>
              <Button type="text" onClick={() => setSource(ref)}>
                {ref.locator || copy.r3_main_view_original}
              </Button>
            </p>
          ))}
        </FoldRow>
      )}
      <SourceEvidence source={source} onClose={() => setSource(undefined)} />
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
  const navigate = useNavigate();
  const query = useResource<{ data: ElementDetail }>('/v2/elements/' + encodeURIComponent(id));
  if (!query.data)
    return (
      <PageLayout variant="archive" header={<PageHeader title={copy.r2_catalog_title} />}>
        <ResourceState error={query.error} reload={query.reload} />
      </PageLayout>
    );
  const detail = query.data.data,
    entity = detail.entity,
    professional = viewMode === 'professional';
  const fields = Object.entries(fieldLabels).flatMap(([key, label]) => {
    const text = businessText(entity.payload[key]);
    return text ? [{ key, label, children: <span className={styles.paragraph}>{text}</span> }] : [];
  });
  const conditions = fields.filter((field) => ['scope', 'conditions', 'constraints'].includes(field.key));
  const activities = (entity.activities || []).flatMap((event) => {
    const label = activityLabel(event.action);
    return label ? [{ ...event, label }] : [];
  });
  const sources = Array.isArray(entity.payload.source_refs) ? entity.payload.source_refs : [];
  return (
    <PageLayout
      variant="archive"
      header={
        <PageHeader
          title={entity.title}
          back={{ label: terms.actions.r2_card_back, href: '/ontology/catalog' }}
          status={
            <>
              <KindTag kind={entity.kind} />
              <StatusTag status={entity.status} />
              {professional && (
                <span>
                  v{entity.version} · {entity.domain}
                </span>
              )}
            </>
          }
          primaryAction={
            entity.status === 'published'
              ? { label: copy.r3_main_ask, onClick: () => navigate(askHref(id)) }
              : detail.can_edit
                ? {
                    label: copy.r3_main_edit_draft,
                    onClick: () => navigate('/assets/' + encodeURIComponent(id)),
                  }
                : undefined
          }
        />
      }
    >
      <div className={styles['archive-columns']}>
        <div className={styles.stack}>
          <BusinessCard detail={detail} />
          <div className={styles.folds}>
            <FoldRow title={copy.r3_main_conditions} summary={copy.r3_main_conditions_summary}>
              <div role="region" aria-label={copy.r3_main_conditions}>
                {conditions.length ? (
                  <Descriptions column={1} items={conditions} />
                ) : (
                  <p>{copy.r2_card_empty}</p>
                )}
              </div>
            </FoldRow>
            <FoldRow
              title={copy.r2_card_relations}
              summary={sentence(copy.r3_main_relation_summary, { count: detail.card.relations.length })}
            >
              <div role="region" aria-label={copy.r2_card_relations}>
                {detail.card.relations.length ? (
                  <ul>
                    {detail.card.relations.map((relation, i) => (
                      <li key={i}>{relation}</li>
                    ))}
                  </ul>
                ) : (
                  <p>{copy.r2_card_empty}</p>
                )}
              </div>
            </FoldRow>
            <FoldRow
              title={copy.r2_card_versions}
              summary={sentence(copy.r3_main_version_summary, { count: detail.versions.length })}
            >
              <div role="region" aria-label={copy.r2_card_versions}>
                {detail.versions.length ? (
                  <ul>
                    {detail.versions.map((version) => (
                      <li key={version.id}>
                        v{version.version} · {formatDateTime(version.created_at)} ·{' '}
                        {version.reason || version.change_note}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>{copy.r2_card_empty}</p>
                )}
              </div>
            </FoldRow>
            <FoldRow
              title={copy.r2_card_activity}
              summary={sentence(copy.r3_main_activity_summary, { count: activities.length })}
            >
              <div role="region" aria-label={copy.r2_card_activity}>
                {activities.length ? (
                  <ul>
                    {activities.map((event) => (
                      <li key={event.id}>
                        {event.label} · {event.detail} · {formatDateTime(event.created_at)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>{copy.r2_card_empty}</p>
                )}
              </div>
            </FoldRow>
            {professional && (
              <>
                <FoldRow
                  title={copy.r2_card_structure}
                  summary={sentence(copy.r3_main_count, { count: fields.length })}
                  defaultOpen
                >
                  <div role="region" aria-label={copy.r2_card_structure}>
                    {fields.length ? <Descriptions column={1} items={fields} /> : <p>{copy.r2_card_empty}</p>}
                  </div>
                </FoldRow>
                <FoldRow
                  title={copy.r3_main_five_checks}
                  summary={copy.r3_main_pro_checks_summary}
                  defaultOpen
                >
                  <ul className={styles.checks}>
                    <li>
                      {copy.r3_main_source_check} ·{' '}
                      {sources.length ? copy.r3_main_check_pass : copy.r3_main_check_pending}
                    </li>
                    <li>
                      {copy.r3_main_content_check} · {copy.r3_main_check_unknown}
                    </li>
                    <li>
                      {copy.r3_main_duplicate_check} · {copy.r3_main_check_unknown}
                    </li>
                    <li>
                      {copy.r3_main_check_scope} ·{' '}
                      {conditions.length ? copy.r3_main_check_pass : copy.r3_main_check_unknown}
                    </li>
                    <li>
                      {copy.r3_main_check_review} ·{' '}
                      {entity.status === 'published' ? copy.r3_main_check_pass : copy.r3_main_check_pending}
                    </li>
                  </ul>
                </FoldRow>
              </>
            )}
            <TechnicalDetails>
              <p>{entity.id}</p>
              <pre>{JSON.stringify(entity.payload, null, 2)}</pre>
            </TechnicalDetails>
          </div>
        </div>
        <aside className={`${styles.card} ${styles.stack}`}>
          <h2>{copy.r2_card_usage}</h2>
          <p>{detail.card.usage}</p>
          <h2>{copy.r3_main_next}</h2>
          <p>{entity.status === 'published' ? copy.r3_main_next_ask : copy.r3_main_draft_note}</p>
          {detail.can_edit && entity.status === 'published' && (
            <Link to={'/assets/' + encodeURIComponent(id)}>{terms.actions.r2_card_edit}</Link>
          )}
        </aside>
      </div>
    </PageLayout>
  );
}
