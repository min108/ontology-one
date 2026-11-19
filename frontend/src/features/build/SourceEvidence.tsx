import { useState } from 'react';
import { Link } from 'react-router-dom';
import { EvidenceDrawer, SourceViewer, TechnicalDetails } from '../../ui';
import { ResourceState, useResource } from '../admin/shared';
import { MaterialReading } from '../ontology/MaterialReaderPage';
import { copy, type Evidence, sourceHref, sourceParams } from './shared';

export function SourceEvidence({
  source,
  citingEntityId,
  onClose,
}: {
  source?: Evidence;
  citingEntityId?: string;
  onClose: () => void;
}) {
  return (
    <EvidenceDrawer
      open={!!source}
      onClose={onClose}
      title={copy.r2_original}
      footer={source && <Link to={sourceHref(source, citingEntityId)}>{copy.r3_main_source_page}</Link>}
    >
      {source &&
        (citingEntityId ? (
          <CitedOriginal
            key={sourceHref(source, citingEntityId)}
            source={source}
            citingEntityId={citingEntityId}
          />
        ) : (
          <Original key={source.material_id + source.source_version + source.anchor_id} source={source} />
        ))}
    </EvidenceDrawer>
  );
}
function CitedOriginal({ source, citingEntityId }: { source: Evidence; citingEntityId: string }) {
  const [page, setPage] = useState({ offset: 0, sourceVersion: source.source_version });
  const params = sourceParams(source, citingEntityId);
  params.set('offset', String(page.offset));
  params.set('source_version', page.sourceVersion);
  return (
    <MaterialReading
      inline
      id={source.material_id}
      params={params}
      onPage={(offset, sourceVersion) => setPage({ offset, sourceVersion })}
    />
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
