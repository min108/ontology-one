import { Alert, Button, Input, Modal } from 'antd';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { invokeCapability } from '../../api/client';
import type { ElementCreate, ElementDetailResult } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { useBuildIdentity, useLifetime } from '../build/shared';
import styles from '../build/Build.module.css';

const copy = terms.labels;
export function ManualAssetCreate({ kind }: { kind: string }) {
  const identity = useBuildIdentity(),
    lifetime = useLifetime(),
    navigate = useNavigate();
  const [open, setOpen] = useState(false),
    [title, setTitle] = useState(''),
    [content, setContent] = useState('');
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  if (!identity.canEdit || !['data', 'rule', 'knowledge'].includes(kind)) return null;
  async function create() {
    const signal = lifetime.current?.signal;
    if (busy || !title.trim() || !content.trim() || signal?.aborted) return;
    setBusy(true);
    setError('');
    try {
      const result = await invokeCapability<ElementDetailResult>(
        'element.create_draft',
        {
          kind,
          title: title.trim(),
          summary: content.trim(),
          payload: { content: content.trim() },
        } satisfies ElementCreate,
        { signal },
      );
      if (!signal?.aborted) navigate('/assets/' + encodeURIComponent(result.data.entity.id));
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : copy.r2_failure);
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }
  return (
    <>
      <Button autoInsertSpace={false} onClick={() => setOpen(true)}>
        {copy.r4a1_legacy_create}
      </Button>
      <Modal
        cancelButtonProps={{ autoInsertSpace: false }}
        open={open}
        title={copy.r4a1_legacy_create_title}
        onCancel={() => !busy && setOpen(false)}
        onOk={() => void create()}
        okText={copy.r4a1_legacy_create_save}
        cancelText={terms.actions.r2_cancel}
        confirmLoading={busy}
        okButtonProps={{ disabled: !title.trim() || !content.trim(), autoInsertSpace: false }}
      >
        <p>{copy.r4a1_legacy_create_note}</p>
        {error && <Alert type="error" role="alert" title={error} />}
        <label className={styles.field}>
          {copy.r4a1_legacy_name}
          <Input
            aria-label={copy.r4a1_legacy_name}
            value={title}
            maxLength={250}
            disabled={busy}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          {copy.r4a1_legacy_content}
          <Input.TextArea
            aria-label={copy.r4a1_legacy_content}
            value={content}
            maxLength={16000}
            rows={5}
            disabled={busy}
            onChange={(event) => setContent(event.target.value)}
          />
        </label>
      </Modal>
    </>
  );
}
