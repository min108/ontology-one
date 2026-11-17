import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import Markdown from '@legacy/components/Markdown';
import { answerReferenceFormatter } from './answerReferences';

const id = 'a'.repeat(32),
  other = 'b'.repeat(32);
const options = {
  assets: [
    { id, title: 'Payment policy' },
    { id: other, title: 'Other policy' },
  ],
  skills: [],
  mappings: [
    { id: 'mapping-1', title: 'Payment records', source_id: 'source-1', source_title: 'Finance source' },
  ],
};
const run = {
  provenance: [
    { entity_id: id, tenant_id: 'hq' },
    { mapping_id: 'mapping-1', source_id: 'source-1', tenant_id: 'hq' },
  ],
};

it('relabels known visible references in every Markdown branch without changing links or original content', () => {
  const content = `# ${id}\n\n${id} **${id}** *${id}* \`${id}\` [${id}](/assets/${id}) [${id}](javascript:blocked)\n\n> ${id}\n\n- ${id}\n- [x] ${id}\n\n| ${id} | Record |\n| --- | --- |\n| ${id} | 30 days |\n\n\`\`\`\n${id}\n\`\`\`\n\nInvoice-2026-30 ${other} mapping-1 source-1`;
  const original = content;
  const { container } = render(
    <Markdown content={content} formatText={answerReferenceFormatter(run, options, 'hq')} />,
  );
  expect(container.textContent).not.toContain(id);
  expect(screen.getByRole('link', { name: 'Payment policy' })).toHaveAttribute('href', '/assets/' + id);
  expect(container.querySelectorAll('a')).toHaveLength(1);
  expect(container.querySelectorAll('code')).toHaveLength(2);
  expect(
    Array.from(container.querySelectorAll('code')).every((node) => node.textContent === 'Payment policy'),
  ).toBe(true);
  expect(container.querySelector('strong')).toHaveTextContent('Payment policy');
  expect(container.querySelector('em')).toHaveTextContent('Payment policy');
  expect(container.querySelector('blockquote')).toHaveTextContent('Payment policy');
  expect(Array.from(container.querySelectorAll('th, td')).map((node) => node.textContent)).toEqual([
    'Payment policy',
    'Record',
    'Payment policy',
    '30 days',
  ]);
  expect(container).toHaveTextContent('Invoice-2026-30 ' + other + ' Payment records Finance source');
  expect(content).toBe(original);
});

it('uses only the matching run and current workspace options, and leaves unknown identifiers intact', () => {
  const formatter = answerReferenceFormatter(run, options, 'hq')!;
  expect(formatter(id + ' ' + other + ' prefix-' + id)).toBe('Payment policy ' + other + ' prefix-' + id);
  expect(answerReferenceFormatter(undefined, options, 'hq')).toBeUndefined();
  const history = answerReferenceFormatter(
    { provenance: [{ entity_id: other, tenant_id: 'hq' }] },
    options,
    'hq',
  )!;
  expect(history(id + ' ' + other)).toBe(id + ' Other policy');
  expect(
    answerReferenceFormatter({ provenance: [{ entity_id: id, tenant_id: 'other-tenant' }] }, options, 'hq'),
  ).toBeUndefined();
  expect(answerReferenceFormatter(run, undefined, 'hq')).toBeUndefined();
  expect(
    answerReferenceFormatter(
      {
        provenance: [
          { entity_id: id, tenant_id: 'hq' },
          { entity_id: id, tenant_id: 'other-tenant' },
        ],
      },
      options,
      'hq',
    ),
  ).toBeUndefined();
});

it('keeps formatter output as plain text and leaves every existing Markdown caller unchanged by default', () => {
  const unsafeTitle = '<img src=x onerror=alert(1)> **literal**';
  const { container, rerender } = render(
    <Markdown
      content={'**' + id + '**'}
      formatText={answerReferenceFormatter(run, { ...options, assets: [{ id, title: unsafeTitle }] }, 'hq')}
    />,
  );
  expect(container.querySelector('strong')).toHaveTextContent(unsafeTitle);
  expect(container.querySelector('img')).toBeNull();
  rerender(<Markdown content={'`' + id + '` [' + id + '](/assets/' + id + ')'} />);
  expect(container.querySelector('code')).toHaveTextContent(id);
  expect(screen.getByRole('link', { name: id })).toHaveAttribute('href', '/assets/' + id);
});
