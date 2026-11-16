import { render, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useDownloadWorkspace } from './useDownloadWorkspace';

function Links({ tenantId, late = false }: { tenantId: string; late?: boolean }) {
  useDownloadWorkspace(tenantId);
  return (
    <>
      <a data-testid="download" href="/api/materials/sample/export?format=md#section">
        download
      </a>
      <a data-testid="external" href="https://example.invalid/api/export?workspace_id=foreign">
        external
      </a>
      <a data-testid="page" href="/materials/sample?workspace_id=foreign">
        page
      </a>
      {late && (
        <a data-testid="late" href="/api/assets/sample/export?workspace_id=previous">
          late
        </a>
      )}
    </>
  );
}

describe('legacy download tenant binding', () => {
  it('binds same-origin API links without changing page or external URLs', () => {
    const view = render(<Links tenantId="dept01" />);
    expect(view.getByTestId('download')).toHaveAttribute(
      'href',
      '/api/materials/sample/export?format=md&workspace_id=dept01#section',
    );
    expect(view.getByTestId('external')).toHaveAttribute(
      'href',
      'https://example.invalid/api/export?workspace_id=foreign',
    );
    expect(view.getByTestId('page')).toHaveAttribute('href', '/materials/sample?workspace_id=foreign');
  });
  it('binds links inserted after route content loads and updates the tenant', async () => {
    const view = render(<Links tenantId="dept01" />);
    view.rerender(<Links tenantId="dept01" late />);
    await waitFor(() =>
      expect(view.getByTestId('late')).toHaveAttribute(
        'href',
        '/api/assets/sample/export?workspace_id=dept01',
      ),
    );
    view.rerender(<Links tenantId="sub01" late />);
    expect(view.getByTestId('late')).toHaveAttribute('href', '/api/assets/sample/export?workspace_id=sub01');
  });
});
