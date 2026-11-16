import { Descriptions, Tabs } from 'antd';
import { Link, useParams } from 'react-router-dom';
import { terms } from '../../i18n/zh-CN';
import { PageHeader } from '../../ui/PageHeader';
import { StatusTag } from '../../ui/StatusTag';
import { ResourceState, useResource } from '../admin/shared';
import { sourceHref, type Evidence, useBuildIdentity } from '../build/shared';
import styles from '../build/Build.module.css';

const copy = terms.labels;
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
  const query = useResource<{ data: ElementDetail }>('/v2/elements/' + encodeURIComponent(id));
  if (!query.data) return <ResourceState error={query.error} reload={query.reload} />;
  const detail = query.data.data,
    entity = detail.entity;
  const labels: Record<string, string> = terms.kinds;
  return (
    <div className={styles.page}>
      <Link to="/ontology/catalog">{terms.actions.r2_card_back}</Link>
      <PageHeader title={entity.title} status={<StatusTag status={entity.status} />} />
      <p>
        {labels[entity.kind]} · {entity.id} · v{entity.version} · {entity.domain}
      </p>
      <BusinessCard detail={detail} />
      {detail.can_edit && <Link to={'/assets/' + encodeURIComponent(id)}>{terms.actions.r2_card_edit}</Link>}
      <Tabs
        items={[
          {
            key: 'structure',
            label: copy.r2_card_structure,
            children: (
              <Descriptions
                column={1}
                items={Object.entries(entity.payload)
                  .filter(([key]) => key !== 'source_refs' && key !== 'r2_review')
                  .map(([key, value]) => ({
                    key,
                    label: key,
                    children: (
                      <span className={styles.paragraph}>
                        {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
                      </span>
                    ),
                  }))}
              />
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
                    v{version.version} · {version.created_at} · {version.reason || version.change_note}
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
            children: entity.activities?.length ? (
              <ul>
                {entity.activities.map((event) => (
                  <li key={event.id}>
                    {event.action} · {event.detail} · {event.created_at}
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
