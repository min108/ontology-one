import { terms } from '../../i18n/zh-CN';
import { TechnicalDetails } from '../../ui';
import { sentence } from './model';

const copy = terms.labels;
const types: Record<string, string> = {
  object: copy.r4a1_schema_object,
  array: copy.r4a1_schema_array,
  string: copy.r4a1_schema_string,
  number: copy.r4a1_schema_number,
  integer: copy.r4a1_schema_integer,
  boolean: copy.r4a1_schema_boolean,
  null: copy.r4a1_schema_null,
};
type Schema = Record<string, unknown>;
const object = (value: unknown): Schema =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Schema) : {};
const description = (value: unknown) =>
  typeof value === 'string' && /[\u3400-\u9fff]/.test(value) && !/[{}]/.test(value) ? value.trim() : '';
const typeName = (schema: Schema): string => {
  const value = schema.type || (schema.properties ? 'object' : schema.items ? 'array' : '');
  return (Array.isArray(value) ? value : [value])
    .map((type) => types[String(type)] || copy.r4a1_schema_unknown)
    .join(' / ');
};
function constraints(schema: Schema): string[] {
  const lines: string[] = [];
  for (const [key, template] of Object.entries({
    minimum: copy.r4a1_schema_min,
    maximum: copy.r4a1_schema_max,
    minLength: copy.r4a1_schema_min_length,
    maxLength: copy.r4a1_schema_max_length,
    minItems: copy.r4a1_schema_min_items,
    maxItems: copy.r4a1_schema_max_items,
  }))
    if (typeof schema[key] === 'number' && Number.isFinite(schema[key]))
      lines.push(sentence(template, { value: schema[key] }));
  if (
    Array.isArray(schema.enum) &&
    schema.enum.every((value) => value === null || ['string', 'number', 'boolean'].includes(typeof value))
  )
    lines.push(
      sentence(copy.r4a1_schema_values, {
        values: schema.enum
          .map((value) =>
            value === null
              ? copy.r4a1_schema_null
              : value === true
                ? copy.r4a1_schema_yes
                : value === false
                  ? copy.r4a1_schema_no
                  : String(value),
          )
          .join('、'),
      }),
    );
  if (schema.additionalProperties === false) lines.push(copy.r4a1_schema_closed);
  return lines;
}
export function SchemaFacts({ value, direction }: { value: string; direction: 'inputs' | 'outputs' }) {
  let schema: Schema = {};
  try {
    schema = object(JSON.parse(value));
  } catch {
    /* Unreadable structures belong only in technical details. */
  }
  const rows: { title: string; details: string[] }[] = [];
  function visit(node: Schema, prefix = '', depth = 0) {
    if (depth > 4 || rows.length >= 60) return;
    const required = Array.isArray(node.required) ? node.required : [];
    for (const [key, child] of Object.entries(object(node.properties))) {
      if (rows.length >= 60) break;
      const field = object(child);
      const label =
        description(field.title) ||
        description(field.description) ||
        sentence(direction === 'inputs' ? copy.r4a1_schema_input : copy.r4a1_schema_output, {
          count: rows.length + 1,
        });
      const title = prefix ? `${prefix} · ${label}` : label;
      rows.push({
        title,
        details: [
          typeName(field),
          required.includes(key) ? copy.r4a1_schema_required : copy.r4a1_schema_optional,
          ...(description(field.title) && description(field.description)
            ? [description(field.description)]
            : []),
          ...constraints(field),
        ],
      });
      visit(field.type === 'array' ? object(field.items) : field, title, depth + 1);
    }
    if (node.type === 'array') visit(object(node.items), prefix, depth + 1);
  }
  visit(schema);
  return (
    <div data-schema-business>
      <p>{Object.keys(schema).length ? typeName(schema) : copy.r4a1_schema_pending}</p>
      {description(schema.description) && <p>{description(schema.description)}</p>}
      {constraints(schema).map((line) => (
        <p key={line}>{line}</p>
      ))}
      {rows.length > 0 && (
        <ul>
          {rows.map((row, index) => (
            <li key={index}>
              <strong>{row.title}</strong>
              <p>{row.details.join('；')}</p>
            </li>
          ))}
        </ul>
      )}
      <TechnicalDetails>
        <pre>{value}</pre>
      </TechnicalDetails>
    </div>
  );
}
