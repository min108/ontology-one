import { FileText, FlaskConical, Sparkles } from 'lucide-react';
import { terms } from '../i18n/zh-CN';
import styles from './Foundation.module.css';
export function OriginTag({
  origin,
  reviewed = false,
}: {
  origin: 'ai' | 'local' | 'demo' | 'mock';
  reviewed?: boolean;
}) {
  const Icon = origin === 'ai' ? Sparkles : origin === 'local' ? FileText : FlaskConical;
  const label =
    origin === 'ai'
      ? reviewed
        ? terms.labels.r3_ui_ai_reviewed
        : terms.labels.r3_ui_ai
      : origin === 'local'
        ? terms.labels.r3_ui_local
        : origin === 'mock'
          ? terms.labels.r3_ui_mock
          : terms.labels.r3_ui_demo;
  return (
    <span
      data-origin={origin}
      className={`${styles.origin} ${origin === 'mock' || origin === 'demo' ? styles.demonstration : ''}`}
    >
      <Icon aria-hidden="true" />
      {label}
    </span>
  );
}
