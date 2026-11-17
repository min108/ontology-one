import { Button, Skeleton } from 'antd';
import { useEffect, useState } from 'react';
import { CircleAlert, FolderOpen, LockKeyhole, Settings2, CircleCheck } from 'lucide-react';
import { terms, type StateKind } from '../i18n/zh-CN';
import type { Action } from './types';
import styles from './StateBlock.module.css';
import { TechnicalDetails } from './TechnicalDetails';
export interface StateBlockProps {
  state: StateKind;
  description?: string;
  action: Action;
  requestId?: string;
  phase?: string;
  title?: string;
}
export function StateBlock({ state, description, action, requestId, phase, title }: StateBlockProps) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (state !== 'loading') return;
    const short = window.setTimeout(() => setElapsed(300), 300);
    const long = window.setTimeout(() => setElapsed(3000), 3000);
    return () => {
      window.clearTimeout(short);
      window.clearTimeout(long);
      setElapsed(0);
    };
  }, [state]);
  if (state === 'loading' && elapsed < 300) return <div aria-busy="true" />;
  const copy = terms.states[state];
  const Icon =
    state === 'forbidden'
      ? LockKeyhole
      : state === 'unconfigured'
        ? Settings2
        : state === 'empty'
          ? FolderOpen
          : state === 'partial'
            ? CircleCheck
            : CircleAlert;
  return (
    <section
      className={styles.block}
      role={state === 'error' ? 'alert' : 'status'}
      aria-busy={state === 'loading'}
    >
      {state === 'loading' ? (
        <Skeleton active={false} paragraph={{ rows: 3 }} />
      ) : (
        <Icon className={styles.icon} aria-hidden="true" />
      )}
      <h3>{title ?? copy.title}</h3>
      <p>
        {state === 'loading' && elapsed >= 3000
          ? (phase ?? copy.description)
          : (description ?? copy.description)}
      </p>
      <Button autoInsertSpace={false} onClick={action.onClick} disabled={action.disabled}>
        {action.label}
      </Button>
      {requestId && (
        <TechnicalDetails>
          <code>{requestId}</code>
        </TechnicalDetails>
      )}
    </section>
  );
}
