import { StrictMode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ContextTrail, NavigationProvider } from '@legacy/navigation';
import { setWorkspaceTransition } from '../state/workspace-guard';

vi.mock('@legacy/api', () => ({ useApi: () => ({ data: { parents: [] } }) }));
const navigationKey = 'ontology-one-navigation';
let scrollPosition = 0;
let scrollTo: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  window.dispatchEvent(new Event('ontology:scope-cleared'));
  localStorage.clear();
  setWorkspaceTransition('idle');
  scrollPosition = 0;
  vi.spyOn(window, 'scrollY', 'get').mockImplementation(() => scrollPosition);
  vi.spyOn(document.documentElement, 'scrollHeight', 'get').mockReturnValue(5000);
  scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation((options: unknown) => {
    if (typeof options === 'object' && options && 'top' in options) scrollPosition = Number(options.top ?? 0);
  });
});
afterEach(() => vi.restoreAllMocks());

describe('legacy navigation location ownership', () => {
  it.each(['/build', '/graph?q=next', '/graph#next'])(
    'does not restore stale route context over browser destination %s',
    (target) => {
      window.history.replaceState(null, '', target);
      render(
        <StrictMode>
          <BrowserRouter>
            <Routes location="/graph">
              <Route
                path="/graph"
                element={
                  <NavigationProvider>
                    <main>
                      <h1>Previous graph</h1>
                    </main>
                  </NavigationProvider>
                }
              />
            </Routes>
          </BrowserRouter>
        </StrictMode>,
      );
      expect(window.location.pathname + window.location.search + window.location.hash).toBe(target);
      expect(localStorage.getItem(navigationKey)).toBeNull();
      expect(scrollTo).not.toHaveBeenCalled();
    },
  );
  it('preserves normal visit tokens, context returns and saved scroll restoration', async () => {
    window.history.replaceState(null, '', '/graph');
    render(
      <BrowserRouter>
        <NavigationProvider>
          <main>
            <ContextTrail />
            <Routes>
              <Route
                path="/graph"
                element={
                  <>
                    <h1>Graph page</h1>
                    <Link to="/assets">Open assets</Link>
                  </>
                }
              />
              <Route path="/assets" element={<h1>Assets page</h1>} />
            </Routes>
          </main>
        </NavigationProvider>
      </BrowserRouter>,
    );
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('via')).toBeTruthy());
    const original = window.location.pathname + window.location.search;
    const originalToken = new URLSearchParams(window.location.search).get('via')!;
    scrollPosition = 420;
    fireEvent.scroll(window);
    fireEvent.click(screen.getByText('Open assets'));
    await waitFor(() => expect(window.location.pathname).toBe('/assets'));
    const currentToken = new URLSearchParams(window.location.search).get('via')!;
    expect(currentToken).not.toBe(originalToken);
    const stored = JSON.parse(localStorage.getItem(navigationKey)!);
    expect(stored[currentToken].parent).toBe(originalToken);
    expect(stored[originalToken].scroll).toBe(420);
    const back = screen.getByRole('link', { name: /返回/ });
    expect(back).toHaveAttribute('href', original);
    fireEvent.click(back);
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe(original));
    await waitFor(() => expect(scrollTo).toHaveBeenCalledWith({ left: 0, top: 420, behavior: 'instant' }));
  });
});
