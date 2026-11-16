import { Alert, Button, Input, Select } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { PageHeader } from '../../ui/PageHeader';
import { BuildForbidden } from './shared';
import { StatusTag } from '../../ui/StatusTag';
import { terms } from '../../i18n/zh-CN';
import { ResourceState, useResource } from '../admin/shared';
import { actions, copy, type Review, sourceHref, useBuildIdentity, useLifetime } from './shared';
import styles from './Build.module.css';

export default function ReviewsPage() {
  const identity = useBuildIdentity();
  return identity.canRead ? <Inbox key={identity.key} /> : <BuildForbidden />;
}
function Inbox() {
  const [kind, setKind] = useState(''),
    [selected, setSelected] = useState('');
  const list = useResource<{ data: { items: Review[] } }>('/v2/reviews?kind=' + kind);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState(''),
    [reason, setReason] = useState('');
  const lifetime = useLifetime();
  const rows = list.data?.data.items;
  const current = rows?.find((row) => row.entity.id === selected) || rows?.[0];
  async function decide(decision: 'approve_publish' | 'return') {
    const signal = lifetime.current?.signal;
    if (!current?.can_decide || busy || signal?.aborted) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await api('/v2/reviews/' + encodeURIComponent(current.entity.id) + ':decide', {
        method: 'POST',
        signal,
        body: JSON.stringify({ decision, version: current.entity.version, reason }),
      });
      if (signal?.aborted) return;
      setMessage(decision === 'return' ? copy.r2_returned : copy.r2_reviewed);
      setReason('');
      setSelected('');
      list.reload();
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : copy.r2_failure);
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }
  return (
    <div className={styles.page}>
      <PageHeader title={copy.r2_reviews_title} />
      <p>{copy.r2_review_help}</p>
      {error && <Alert type="error" title={error} role="alert" />}
      {message && (
        <Alert
          type="success"
          title={message}
          role="status"
          action={<Link to="/applications">{actions.r2_ask}</Link>}
        />
      )}
      <div className={styles.actions}>
        <Select
          aria-label={copy.r2_kind}
          value={kind}
          onChange={(value) => {
            setKind(value);
            setSelected('');
            setReason('');
          }}
          options={[
            { value: '', label: copy.r2_all_kinds },
            ...(['rule', 'data', 'knowledge', 'skill'] as const).map((value) => ({
              value,
              label: terms.kinds[value],
            })),
          ]}
        />
        <Button autoInsertSpace={false} onClick={list.reload}>
          {actions.r2_refresh}
        </Button>
      </div>
      {!rows ? (
        <ResourceState error={list.error} reload={list.reload} />
      ) : !rows.length ? (
        <p>{copy.r2_empty_reviews}</p>
      ) : (
        <div className={styles.columns}>
          <div className={styles.stack} aria-label={copy.r2_select_review}>
            {rows.map((row) => (
              <section key={row.entity.id} className={styles.card}>
                <StatusTag status={row.entity.status} />
                <h2>
                  <Button
                    autoInsertSpace={false}
                    type="link"
                    onClick={() => {
                      setSelected(row.entity.id);
                      setReason('');
                    }}
                  >
                    {row.entity.title}
                  </Button>
                </h2>
                <p>{row.entity.summary}</p>
                <p className={styles.muted}>
                  {copy.r2_submitter} · {row.submitter}
                </p>
              </section>
            ))}
          </div>
          {current && (
            <article className={styles.card} data-review-id={current.entity.id}>
              <h2>{current.entity.title}</h2>
              <p>
                {current.entity.payload.content ||
                  current.entity.payload.definition ||
                  current.entity.summary}
              </p>
              {current.entity.payload.conditions?.map((condition) => (
                <p key={condition}>{condition}</p>
              ))}
              <h3>{copy.r2_original}</h3>
              {current.source_refs.map((ref) => (
                <blockquote key={ref.anchor_id} className={styles.quote}>
                  {ref.quote}
                  <p>
                    <Link to={sourceHref(ref)}>{copy.r2_source_open}</Link>
                  </p>
                </blockquote>
              ))}
              <p>
                {copy.r2_submitter} · {current.submitter}
              </p>
              {current.blocked_reason && <Alert type="info" title={current.blocked_reason} />}
              <label className={styles.field}>
                {copy.r2_reason}
                <Input.TextArea
                  value={reason}
                  rows={3}
                  maxLength={500}
                  placeholder={copy.r2_reason_hint}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <div className={styles.actions}>
                <Button
                  autoInsertSpace={false}
                  type="primary"
                  loading={busy}
                  disabled={!current.can_decide}
                  onClick={() => void decide('approve_publish')}
                >
                  {actions.catalog_2_5}
                </Button>
                <Button
                  autoInsertSpace={false}
                  disabled={!current.can_decide || busy || !reason.trim()}
                  onClick={() => void decide('return')}
                >
                  {actions.catalog_2_6}
                </Button>
              </div>
            </article>
          )}
        </div>
      )}
    </div>
  );
}
