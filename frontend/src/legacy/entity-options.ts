/** Material pickers link originals. A ranked source hit identifies its chunk in the legacy payload. */
export function selectableEntities<T extends { id: string; kind: string; payload: unknown }>(
  rows: T[] = [],
): T[] {
  const options: T[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    let id = row.id;
    const payload = row.payload;
    if (
      row.kind === 'material' &&
      payload &&
      typeof payload === 'object' &&
      'chunk_id' in payload &&
      payload.chunk_id === row.id
    ) {
      if (
        !('material_id' in payload) ||
        typeof payload.material_id !== 'string' ||
        !payload.material_id.trim()
      )
        continue;
      id = payload.material_id;
    }
    if (!seen.has(id)) {
      options.push(id === row.id ? row : { ...row, id });
      seen.add(id);
    }
  }
  return options;
}
