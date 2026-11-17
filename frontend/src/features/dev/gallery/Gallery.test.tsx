import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it } from 'vitest';
import { useUiStore } from '../../../state/ui';
import { terms } from '../../../i18n/zh-CN';
import Gallery from './Gallery';

beforeEach(() => useUiStore.setState({ viewMode: 'simple' }));
const show = () =>
  render(
    <MemoryRouter>
      <Gallery />
    </MemoryRouter>,
  );

it('uses one shared page skeleton without a separate identity stripe and retains searchable rows', async () => {
  const { container } = show();
  expect(container.querySelectorAll('[data-page-template]')).toHaveLength(1);
  expect(
    screen.getByRole('heading', { name: terms.labels.gallery }).closest('[data-page-header]'),
  ).not.toBeNull();
  expect(container.querySelector('[data-page-content]')).not.toBeNull();
  const main = container.querySelector('main')!;
  expect(main.parentElement?.firstElementChild?.children).toHaveLength(1);
  expect(main.parentElement?.firstElementChild?.firstElementChild).toHaveAttribute('data-topbar');
  expect(main.previousElementSibling?.tagName).toBe('ASIDE');
  expect(main.firstElementChild).toHaveAttribute('data-page-template');
  fireEvent.change(screen.getByLabelText(terms.labels.filter), { target: { value: 'no-matching-sample' } });
  expect(screen.queryByRole('row', { name: new RegExp(terms.labels.sample_title) })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: terms.actions.reset }));
  expect(screen.getByRole('row', { name: new RegExp(terms.labels.sample_title) })).toBeInTheDocument();
});

it('shows the five source fields for a complete number and gives incomplete numbers no interaction', async () => {
  show();
  fireEvent.click(screen.getByRole('button', { name: terms.actions.r3_ui_evidence }));
  const evidence = await screen.findByRole('dialog');
  const complete = within(evidence).getByTestId('gallery-sourced-complete');
  fireEvent.click(within(complete).getByRole('button', { name: '12' }));
  for (const label of [
    terms.labels.r3_ui_source_label,
    terms.labels.r3_ui_source_time,
    terms.labels.r3_ui_source_caliber,
    terms.labels.r3_ui_source_version,
    terms.labels.r3_ui_source_owner,
  ]) {
    expect(await screen.findByText(label, { exact: true, selector: 'dt' })).toBeInTheDocument();
  }
  for (const value of [
    terms.labels.r3_gallery_source_title,
    terms.labels.r3_gallery_source_time,
    terms.labels.r3_gallery_source_caliber,
    terms.labels.r3_gallery_source_version,
    terms.labels.sample_person,
  ])
    expect(screen.getByText(value, { selector: 'dd' })).toBeInTheDocument();
  const partial = within(evidence).getByTestId('gallery-sourced-incomplete');
  expect(partial).toHaveTextContent('8');
  expect(within(partial).queryByRole('button')).not.toBeInTheDocument();
  expect(partial.querySelector('[tabindex]')).toBeNull();
});

it('opens an exact source quote in the drawer and keeps raw data exclusive to professional view', async () => {
  const { container } = show();
  fireEvent.click(screen.getByRole('button', { name: terms.actions.r3_gallery_open_source }));
  const drawer = await screen.findByRole('dialog');
  expect(drawer.querySelector('mark')).toHaveTextContent(terms.labels.r3_gallery_quote);
  expect(drawer.textContent).not.toContain('gallery-source-1');
  useUiStore.setState({ viewMode: 'professional' });
  fireEvent.click(await within(drawer).findByText(terms.labels.r3_ui_technical));
  expect(within(drawer).getByText('gallery-source-1')).toBeInTheDocument();
  expect(container.querySelector('input[type="date"]')).toBeNull();
});

it('retains all state demos and routes overflow actions to visible feedback', async () => {
  show();
  for (const state of ['empty', 'forbidden', 'error', 'unconfigured', 'partial'] as const)
    expect(screen.getByRole('heading', { name: terms.states[state].title })).toBeInTheDocument();
  expect(await screen.findByRole('heading', { name: terms.states.loading.title })).toBeInTheDocument();
  const row = screen.getByRole('row', { name: new RegExp(terms.labels.sample_title) });
  fireEvent.click(within(row).getByRole('button', { name: terms.actions.r3_ui_more }));
  fireEvent.click(await screen.findByRole('menuitem', { name: terms.actions.compare }));
  expect(screen.getByTestId('gallery-feedback')).toHaveTextContent(terms.actions.compare);
});
