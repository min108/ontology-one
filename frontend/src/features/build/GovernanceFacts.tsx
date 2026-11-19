import { Link } from 'react-router-dom';
import type { GateResult } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import styles from './Build.module.css';
import { SchemaFacts } from './SchemaFacts';
import { isAnchoredEvidence } from './model';
import { sourceHref } from './shared';

const copy = terms.labels;
const origins = {
  source: copy.r4a1_legacy_origin_source,
  system: copy.r4a2_origin_system,
  editor: copy.r4a1_legacy_origin_editor,
  batch_confirmation: copy.r4a1_legacy_origin_batch,
  legacy: copy.r4a1_legacy_origin_legacy,
  missing: copy.r4a1_legacy_origin_missing,
};

export function GovernanceFacts({
  gates,
  showHeading = true,
}: {
  gates?: GateResult | null;
  showHeading?: boolean;
}) {
  if (!gates?.fields?.length) return null;
  return (
    <section aria-label={copy.r4a1_legacy_facts} data-governance-facts>
      {showHeading && <h3>{copy.r4a1_legacy_facts}</h3>}
      <dl className={styles.stack}>
        {gates.fields.map((field) => {
          const evidence = { ...field, quote: field.source_quote };
          return (
            <div key={field.key} className={styles.metadata} data-governance-field={field.key}>
              <dt>{field.label}</dt>
              <dd>
                {field.key === 'inputs' || field.key === 'outputs' ? (
                  <SchemaFacts value={field.value} direction={field.key} />
                ) : field.href?.startsWith('/assets/') ? (
                  <Link to={field.href}>{field.value || copy.r4a1_legacy_empty}</Link>
                ) : (
                  <span className={styles.paragraph}>{field.value || copy.r4a1_legacy_empty}</span>
                )}
                <p className={styles.muted} data-fact-origin={field.source}>
                  {field.source === 'editor' && field.method === 'selection'
                    ? copy.r4a2_origin_selection
                    : origins[field.source]}
                  {field.confirmed_by ? ` · ${field.confirmed_by}` : ''}
                </p>
                {field.source_quote && (
                  <blockquote className={styles.quote}>
                    {isAnchoredEvidence(evidence) ? (
                      <Link to={sourceHref(evidence)}>{field.source_quote}</Link>
                    ) : (
                      field.source_quote
                    )}
                  </blockquote>
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}
