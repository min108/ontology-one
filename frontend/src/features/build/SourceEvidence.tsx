import { Link } from 'react-router-dom';
import { EvidenceDrawer, SourceViewer, TechnicalDetails } from '../../ui';
import { ResourceState, useResource } from '../admin/shared';
import { copy, type Evidence, sourceHref } from './shared';

export function SourceEvidence({ source, onClose }: { source?: Evidence; onClose: () => void }) {
  return (
    <EvidenceDrawer
      open={!!source}
      onClose={onClose}
      title={copy.r2_original}
      footer={source && <Link to={sourceHref(source)}>{copy.r3_main_source_page}</Link>}
    >
      {source && (
        <Original key={source.material_id + source.source_version + source.anchor_id} source={source} />
      )}
    </EvidenceDrawer>
  );
}
function Original({ source }: { source: Evidence }) {
  const query = useResource<{
    text: string;
    focus?: { start: number; end: number; quote: string };
    source_version: string;
  }>(
    '/materials/' +
      encodeURIComponent(source.material_id) +
      '/source?' +
      new URLSearchParams({
        source_version: source.source_version,
        anchor_id: source.anchor_id,
        quote: source.quote,
      }),
  );
  return (
    <>
      {query.data ? (
        <SourceViewer
          text={query.data.text}
          quote={source.quote}
          start={query.data.focus?.start ?? source.start}
          end={query.data.focus?.end ?? source.end}
        />
      ) : (
        <>
          <SourceViewer
            text={source.quote}
            quote={source.quote}
            start={0}
            end={Array.from(source.quote).length}
          />
          <ResourceState error={query.error} reload={query.reload} />
        </>
      )}
      <TechnicalDetails>
        <p>{source.source_version}</p>
        <p>{source.anchor_id}</p>
        <p>
          {source.start}–{source.end}
        </p>
      </TechnicalDetails>
    </>
  );
}
