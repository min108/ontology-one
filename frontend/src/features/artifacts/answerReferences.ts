type Reference = {
  entity_id?: string;
  skill_id?: string;
  mapping_id?: string;
  source_id?: string;
  tenant_id?: string;
  title?: string;
  source_title?: string;
};
type Option = { id: string; title: string; source_id?: string; source_title?: string };
type Options = { assets: Option[]; skills: Option[]; mappings: Option[] };

/** Presentation only: names come from this run's receipts and the current authorized catalog. */
export function answerReferenceFormatter(
  run: { provenance?: Reference[] } | null | undefined,
  options: Options | null | undefined,
  currentTenantId: string,
): ((text: string) => string) | undefined {
  const labels = new Map<string, string | null>();
  const owners = new Map<string, string>();
  const add = (id: string | undefined, title: string | undefined) => {
    if (!id || !title?.trim()) return;
    labels.set(id, labels.has(id) && labels.get(id) !== title ? null : title);
  };
  for (const source of run?.provenance ?? []) {
    const owner = source.tenant_id || currentTenantId;
    for (const id of [source.entity_id, source.skill_id, source.mapping_id, source.source_id]) {
      if (!id) continue;
      if (owners.has(id) && owners.get(id) !== owner) labels.set(id, null);
      else owners.set(id, owner);
    }
    // A local identifier can also exist in another institution. Never join those catalogs.
    const local = !!currentTenantId && (!source.tenant_id || source.tenant_id === currentTenantId);
    const entity = local
      ? [...(options?.assets ?? []), ...(options?.skills ?? [])].find(
          (item) => item.id === (source.entity_id || source.skill_id),
        )
      : undefined;
    const mapping = local ? options?.mappings.find((item) => item.id === source.mapping_id) : undefined;
    add(source.entity_id, source.title || entity?.title);
    add(source.skill_id, source.title || entity?.title);
    add(source.mapping_id, source.title || mapping?.title);
    add(
      source.source_id,
      source.source_title || (mapping?.source_id === source.source_id ? mapping?.source_title : undefined),
    );
  }
  const identifiers = [...labels.keys()].filter((id) => labels.get(id)).sort((a, b) => b.length - a.length);
  if (!identifiers.length) return undefined;
  const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const known = new RegExp(
    '(?<![A-Za-z0-9_-])(?:' + identifiers.map(escape).join('|') + ')(?![A-Za-z0-9_-])',
    'g',
  );
  // One pass prevents a title from being interpreted as another identifier or Markdown.
  return (text) => text.replace(known, (identifier) => labels.get(identifier)!);
}
