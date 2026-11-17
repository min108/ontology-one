import { Alert, Button, Input, Modal, Select } from 'antd';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { invokeCapability } from '../../api/client';
import { PageHeader } from '../../ui/PageHeader';
import { StatusTag } from '../../ui/StatusTag';
import { BuildForbidden } from './shared';
import { terms } from '../../i18n/zh-CN';
import { ResourceState, useResource } from '../admin/shared';
import {
  actions,
  copy,
  type Candidate,
  type Evidence,
  type Item,
  type Proposal,
  sourceHref,
  useBuildIdentity,
  useLifetime,
} from './shared';
import styles from './Build.module.css';

export default function ChangesetPage() {
  const { id = '' } = useParams();
  const identity = useBuildIdentity();
  return identity.canRead ? (
    <Changeset key={identity.key + id} id={id} canEdit={identity.canEdit} />
  ) : (
    <BuildForbidden />
  );
}
function Changeset({ id, canEdit }: { id: string; canEdit: boolean }) {
  const resource = useResource<{ data: Proposal }>('/v2/changesets/' + encodeURIComponent(id));
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState('');
  const [focus, setFocus] = useState<Evidence>();
  const [editing, setEditing] = useState<Item>();
  const [draft, setDraft] = useState<Candidate>({ title: '', content: '', kind: 'rule' });
  const lifetime = useLifetime();
  const proposal = resource.data?.data;
  async function perform(operation: () => Promise<unknown>, success: string) {
    if (!canEdit || busy || resource.refreshing || resource.error || lifetime.current?.signal.aborted) return;
    const signal = lifetime.current?.signal;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await operation();
      if (signal?.aborted) return;
      setMessage(success);
      setEditing(undefined);
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : copy.r2_failure);
    } finally {
      if (!signal?.aborted) {
        resource.reload();
        setBusy(false);
      }
    }
  }
  function decide(items: Item[], decision: string, edit?: Candidate) {
    return perform(
      async () => {
        const options = { signal: lifetime.current?.signal };
        await invokeCapability(
          'changeset.decide_items',
          {
            changeset_id: id,
            item_ids: items.map((item) => item.id),
            decision,
            ...(edit ? { edit: { title: edit.title, content: edit.content, kind: edit.kind } } : {}),
          },
          options,
        );
        if (['accepted', 'edited'].includes(decision))
          await invokeCapability('changeset.apply', { changeset_id: id }, options);
      },
      decision === 'rejected' ? copy.r2_saved : copy.r2_drafts,
    );
  }
  if (!proposal) return <ResourceState error={resource.error} reload={resource.reload} />;
  const chosen = focus || proposal.items[0]?.evidence[0];
  const characters = Array.from(proposal.source_text);
  const sure = proposal.items.filter(
    (item) => item.tier === 'sure' && item.decision === 'pending' && item.validation.level !== 'error',
  );
  return (
    <div className={styles.page}>
      <PageHeader
        title={copy.r2_changeset_title}
        status={<StatusTag domain="proposal" status={proposal.status} />}
      />
      <p>
        {proposal.title} · {proposal.summary}
      </p>
      {(error || resource.error) && !editing && (
        <Alert
          type="error"
          title={error || resource.error}
          role="alert"
          action={resource.error && <Button onClick={resource.reload}>{copy.admin_retry}</Button>}
        />
      )}
      {message && (
        <Alert
          type="success"
          title={message}
          role="status"
          action={<Link to="/govern/reviews">{copy.r2_review_link}</Link>}
        />
      )}
      {!canEdit && <Alert type="info" title={copy.r2_readonly} />}
      <div className={styles.actions}>
        <Button
          autoInsertSpace={false}
          disabled={!canEdit || busy || resource.refreshing || !!resource.error || !sure.length}
          onClick={() => void decide(sure, 'accepted')}
        >
          {actions.catalog_1_4}
        </Button>
        <Button
          autoInsertSpace={false}
          type="primary"
          loading={busy}
          aria-label={actions.catalog_1_6}
          disabled={!canEdit || busy || resource.refreshing || !!resource.error || !proposal.can_submit}
          onClick={() =>
            void perform(
              () =>
                invokeCapability(
                  'changeset.apply_and_submit',
                  { changeset_id: id },
                  { signal: lifetime.current?.signal },
                ),
              copy.r2_submitted,
            )
          }
        >
          {actions.catalog_1_6}
        </Button>
        <Link to={'/materials/' + encodeURIComponent(proposal.source_material_id)}>
          {copy.r2_source_open}
        </Link>
      </div>
      <div className={styles.columns}>
        <div className={styles.stack}>
          {(['sure', 'check', 'doubt'] as const).map((tier) => (
            <section key={tier} aria-label={terms.statuses.confidence[tier]} className={styles.stack}>
              <h2>
                {terms.statuses.confidence[tier]} ·{' '}
                {proposal.items.filter((item) => item.tier === tier).length}
              </h2>
              {proposal.items
                .filter((item) => item.tier === tier)
                .map((item) => (
                  <article className={styles.card} key={item.id} data-item-id={item.id}>
                    <div className={styles.actions}>
                      <StatusTag domain="decision" status={item.decision} />
                      <span>
                        {terms.kinds[item.after_data.kind as keyof typeof terms.kinds] ||
                          item.after_data.kind}
                      </span>
                    </div>
                    <h3>{item.after_data.title}</h3>
                    <p>{item.one_liner}</p>
                    <p className={styles.muted}>
                      {item.content_origin === 'local' ? copy.r2_local : copy.r2_model}
                    </p>
                    {item.duplicate_of && <Alert type="warning" title={copy.r2_duplicate} />}
                    {item.validation.issues.map((issue) => (
                      <p key={issue} role="alert">
                        {issue}
                      </p>
                    ))}
                    <blockquote className={styles.quote}>
                      {item.evidence[0]?.quote || copy.r2_no_anchor}
                    </blockquote>
                    <div className={styles.actions}>
                      {item.evidence[0] && (
                        <Button autoInsertSpace={false} onClick={() => setFocus(item.evidence[0])}>
                          {actions.r2_preview}
                        </Button>
                      )}
                      {item.applied_entity_id ? (
                        <Link to={'/assets/' + item.applied_entity_id}>{copy.r2_view_draft}</Link>
                      ) : (
                        <>
                          <Button
                            autoInsertSpace={false}
                            disabled={
                              !canEdit ||
                              busy ||
                              resource.refreshing ||
                              !!resource.error ||
                              item.validation.level === 'error'
                            }
                            onClick={() => void decide([item], 'accepted')}
                          >
                            {actions.catalog_1_0}
                          </Button>
                          <Button
                            autoInsertSpace={false}
                            disabled={!canEdit || busy || resource.refreshing || !!resource.error}
                            onClick={() => {
                              setEditing(item);
                              setError('');
                              setDraft({
                                title: item.after_data.title,
                                content: item.after_data.content,
                                kind: item.after_data.kind,
                              });
                            }}
                          >
                            {actions.catalog_1_1}
                          </Button>
                          <Button
                            autoInsertSpace={false}
                            disabled={
                              !canEdit ||
                              busy ||
                              resource.refreshing ||
                              !!resource.error ||
                              item.decision === 'rejected'
                            }
                            onClick={() => void decide([item], 'rejected')}
                          >
                            {actions.catalog_1_2}
                          </Button>
                        </>
                      )}
                    </div>
                  </article>
                ))}
            </section>
          ))}
          {!proposal.items.length && <p>{copy.r2_empty_items}</p>}
        </div>
        <aside className={`${styles.card} ${styles.source}`} aria-label={copy.r2_selected_original}>
          <h2>{copy.r2_original}</h2>
          {chosen && (
            <p>
              <Link to={sourceHref(chosen)}>{copy.r2_source_open}</Link>
            </p>
          )}
          {chosen ? (
            <>
              {characters.slice(0, chosen.start).join('')}
              <mark>{characters.slice(chosen.start, chosen.end).join('')}</mark>
              {characters.slice(chosen.end).join('')}
            </>
          ) : (
            proposal.source_text
          )}
        </aside>
      </div>
      <Modal
        title={actions.catalog_1_1}
        open={Boolean(editing)}
        onCancel={() => setEditing(undefined)}
        okText={actions.r2_save}
        cancelText={actions.r2_cancel}
        confirmLoading={busy}
        okButtonProps={{ disabled: resource.refreshing || !!resource.error, 'aria-label': actions.r2_save }}
        onOk={() => editing && void decide([editing], 'edited', draft)}
      >
        {(error || resource.error) && (
          <Alert
            type="error"
            title={error || resource.error}
            role="alert"
            action={resource.error && <Button onClick={resource.reload}>{copy.admin_retry}</Button>}
          />
        )}
        <label className={styles.field}>
          {copy.r2_title}
          <Input
            value={draft.title}
            maxLength={250}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          {copy.r2_kind}
          <Select
            aria-label={copy.r2_kind}
            value={draft.kind}
            onChange={(kind) => setDraft({ ...draft, kind })}
            options={(['rule', 'data', 'knowledge', 'skill'] as const).map((kind) => ({
              value: kind,
              label: terms.kinds[kind],
            }))}
          />
        </label>
        <label className={styles.field}>
          {copy.r2_content}
          <Input.TextArea
            rows={7}
            value={draft.content}
            maxLength={16000}
            onChange={(event) => setDraft({ ...draft, content: event.target.value })}
          />
        </label>
      </Modal>
    </div>
  );
}
