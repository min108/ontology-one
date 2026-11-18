import { Link } from 'react-router-dom';
import type { GateResult } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import styles from './Build.module.css';

const copy = terms.labels;
const origins = {
  source: copy.r4a1_legacy_origin_source,
  editor: copy.r4a1_legacy_origin_editor,
  batch_confirmation: copy.r4a1_legacy_origin_batch,
  legacy: copy.r4a1_legacy_origin_legacy,
  missing: copy.r4a1_legacy_origin_missing,
};

export function GovernanceFacts({ gates }: { gates?: GateResult | null }) {
  if (!gates?.fields?.length) return null;
  return (
    <section aria-label={copy.r4a1_legacy_facts} data-governance-facts>
      <h3>{copy.r4a1_legacy_facts}</h3>
      <dl className={styles.stack}>
        {gates.fields.map((field) => (
          <div key={field.key} className={styles.metadata} data-governance-field={field.key}>
            <dt>{field.label}</dt>
            <dd>
              {field.href?.startsWith('/assets/') ? (
                <Link to={field.href}>{field.value || copy.r4a1_legacy_empty}</Link>
              ) : (
                <span className={styles.paragraph}>{field.value || copy.r4a1_legacy_empty}</span>
              )}
              <p className={styles.muted} data-fact-origin={field.source}>
                {origins[field.source]}
                {field.confirmed_by ? ` · ${field.confirmed_by}` : ''}
              </p>
              {field.source_quote && <blockquote className={styles.quote}>{field.source_quote}</blockquote>}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
