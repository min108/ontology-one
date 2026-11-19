import { Button } from 'antd';
import type { Proposal, RetainSource } from '../../api/generated/types';
import { Link } from 'react-router-dom';
import { copy, sourceHref } from './shared';
import sourceStyles from './ChangesetUnformedSources.module.css';
import styles from './Build.module.css';

export function ChangesetUnformedSources({
  proposal,
  disabled,
  onRetain,
}: {
  proposal: Proposal;
  disabled: boolean;
  onRetain: (body: RetainSource) => void;
}) {
  const sources = proposal.unformed_sources || [];
  if (!sources.length) {
    const discarded = proposal.extraction_summary?.discarded_candidates || 0;
    const retained = proposal.items.some((item) => item.after_data.retained_anchor_id);
    return discarded && !retained ? <p>{copy.r4a1_extraction_discarded}</p> : null;
  }
  return (
    <details
      className={sourceStyles.panel}
      data-unformed-sources
      open={sources.some((ref) => ref.unformed_reason === 'invalid_quote')}
    >
      <summary>{copy.r4a2_unformed_sources}</summary>
      <ul className={sourceStyles.sources}>
        {sources.map((ref) => (
          <li key={ref.anchor_id} data-source-anchor={ref.anchor_id}>
            <Link to={sourceHref(ref)}>{ref.locator}</Link>
            <blockquote className={styles.quote}>{ref.quote.trim()}</blockquote>
            <Button
              disabled={disabled}
              onClick={() =>
                onRetain({
                  changeset_id: proposal.id,
                  source_version: ref.source_version,
                  anchor_id: ref.anchor_id,
                })
              }
            >
              {copy.r4a2_keep_source}
            </Button>
          </li>
        ))}
      </ul>
    </details>
  );
}
