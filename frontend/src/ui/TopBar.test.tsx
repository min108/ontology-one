import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { brand } from '../i18n/brand';
import { TopBar } from './TopBar';

describe('production brand and header slots', () => {
  it('keeps the local mark and full bilingual tooltip', () => {
    render(<TopBar workspaceName="Example" />);
    const link = screen.getByRole('link', { name: brand.product_name });
    expect(link).toHaveAttribute('title', `${brand.product_name} · ${brand.product_name_en}`);
    expect(link.querySelector('img')).toHaveAttribute('src', '/citic-mark.svg');
  });
  it('keeps an accessible brand when its label is collapsed', () => {
    render(<TopBar workspaceName="Example" collapsed />);
    const link = screen.getByRole('link', { name: brand.product_name });
    expect(link.querySelector('[hidden]')).toHaveTextContent(brand.product_name);
  });
  it('accepts real identity, search and existing activity controls', () => {
    render(
      <TopBar
        workspaceName="Example"
        identity={<select aria-label="organization" />}
        searchSlot={<input aria-label="search" />}
        actions={<button>Activity</button>}
      />,
    );
    expect(screen.getByRole('combobox', { name: 'organization' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'search' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Activity' })).toBeInTheDocument();
  });
});
