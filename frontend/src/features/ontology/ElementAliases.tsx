import { Alert, Button, Input, Modal } from 'antd';
import { useState } from 'react';
import { invokeCapability } from '../../api/client';
import type {
  ElementAction,
  ElementDetail,
  ElementDetailResult,
  ElementUpdate,
} from '../../api/generated/types';
import { useWorkspaceGuard } from '../../app/useWorkspaceGuard';
import { terms } from '../../i18n/zh-CN';
import { useLifetime } from '../build/shared';
import styles from '../build/Build.module.css';

const copy = terms.labels;

export function aliasesOf(entity: ElementDetail['entity']): string[] {
  const raw = entity.payload.aliases;
  return (Array.isArray(raw) ? raw : [raw]).filter(
    (name): name is string => typeof name === 'string' && Boolean(name.trim()),
  );
}

export function ElementAliases({
  detail,
  disabled,
  onSaved,
}: {
  detail: ElementDetail;
  disabled: boolean;
  onSaved: (message?: string) => void;
}) {
  const { entity } = detail;
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lifetime = useLifetime();
  useWorkspaceGuard(open && Boolean(value.trim()));
  const meta = entity._meta;
  if (
    !detail.can_edit ||
    !meta ||
    typeof meta !== 'object' ||
    !('pillar' in meta) ||
    meta.pillar !== 'asset' ||
    !['published', 'approved', 'draft', 'returned'].includes(entity.status) ||
    entity.payload.authority_spec ||
    entity.payload.managed_skill ||
    entity.payload.active_asset === false
  )
    return null;
  const published = ['published', 'approved'].includes(entity.status);
  const actionLabel = published ? terms.actions.catalog_2_8 : terms.actions.catalog_2_0;
  const aliases = aliasesOf(entity);
  const next = value.trim();
  const duplicate = aliases.some((alias) => alias.trim().toLowerCase() === next.toLowerCase());
  async function apply() {
    const signal = lifetime.current?.signal;
    if (busy || disabled || signal?.aborted || !next || duplicate) return;
    setBusy(true);
    setError('');
    const version = { entity_id: entity.id, version: entity.version, reason: copy.r4b_alias_register };
    try {
      if (published) {
        await invokeCapability<ElementDetailResult>(
          'element.start_revision',
          version satisfies ElementAction,
          { signal },
        );
        if (!signal?.aborted) onSaved();
      } else {
        await invokeCapability<ElementDetailResult>(
          'element.update_draft',
          { ...version, add_alias: next } satisfies ElementUpdate,
          { signal },
        );
        if (!signal?.aborted) {
          setOpen(false);
          setValue('');
          onSaved(copy.r4b_alias_saved);
        }
      }
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : copy.r2_failure);
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }
  return (
    <>
      <Button
        autoInsertSpace={false}
        disabled={disabled}
        onClick={() => {
          setValue('');
          setError('');
          setOpen(true);
        }}
      >
        {copy.r4b_alias_register}
      </Button>
      <Modal
        title={copy.r4b_alias_register}
        open={open}
        onCancel={() => {
          if (!busy) setOpen(false);
        }}
        okText={actionLabel}
        cancelText={terms.actions.cancel}
        onOk={() => void apply()}
        confirmLoading={busy}
        okButtonProps={{ 'aria-label': actionLabel, disabled: disabled || !next || duplicate }}
        cancelButtonProps={{ disabled: busy }}
      >
        {error && <Alert role="alert" type="error" title={error} />}
        {published && <p>{copy.r4b_alias_revision_note}</p>}
        {aliases.length > 0 && (
          <p>
            {copy.r4b_aliases} · {aliases.join('、')}
          </p>
        )}
        <label className={styles.field}>
          {copy.r4b_alias_new}
          <Input
            value={value}
            maxLength={250}
            disabled={disabled || busy}
            onChange={(event) => setValue(event.target.value)}
          />
        </label>
      </Modal>
    </>
  );
}
