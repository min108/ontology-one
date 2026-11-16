import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { addressPage, patchAddressSearch, useAddressState } from './addressState';

function Filter() {
  const { params, update } = useAddressState();
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output>{location.pathname + location.search + location.hash}</output>
      <input
        aria-label="query"
        value={params.get('q') ?? ''}
        onChange={(event) => update({ q: event.target.value, page: null }, { replace: false })}
      />
      <button onClick={() => void navigate(-1)}>back</button>
      <button
        onClick={() => {
          update({ q: 'combined' });
          update({ page: 2 });
        }}
      >
        combine
      </button>
    </>
  );
}

describe('address state', () => {
  it('preserves unrelated context while replacing owned scalar and multi-valued fields', () => {
    expect(
      patchAddressSearch('?context=task-1&page=4&kind=old', { page: 2, kind: ['rule', 'skill'], q: '' }),
    ).toBe('?context=task-1&page=2&kind=rule&kind=skill');
    expect(patchAddressSearch('?page=4', { page: null })).toBe('');
  });
  it('rejects unsafe or nonpositive page numbers', () => {
    for (const value of [null, '', '0', '-1', '1.5', '1e3', '9007199254740992'])
      expect(addressPage(value)).toBe(1);
    expect(addressPage('12')).toBe(12);
  });
  it('restores filters on browser back without losing anchors', async () => {
    render(
      <MemoryRouter initialEntries={['/assets?q=old&page=3#details']}>
        <Filter />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('query'), { target: { value: 'new' } });
    expect(screen.getByRole('status')).toHaveTextContent('/assets?q=new#details');
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    expect(await screen.findByDisplayValue('old')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('/assets?q=old&page=3#details');
  });
  it('merges consecutive updates in one event without overwriting filters', () => {
    render(
      <MemoryRouter initialEntries={['/assets?q=old#details']}>
        <Filter />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'combine' }));
    expect(screen.getByRole('status')).toHaveTextContent('/assets?q=combined&page=2#details');
  });
});
