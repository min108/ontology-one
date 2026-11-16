export const routeNavigationRequestEvent = 'ontology:request-route-navigation';

/** Let an existing route editor cancel a new programmatic navigation entry point. */
export function requestRouteNavigation(href: string, navigate: (href: string) => void): boolean {
  const request = new CustomEvent<{ href: string }>(routeNavigationRequestEvent, {
    cancelable: true,
    detail: { href },
  });
  if (!window.dispatchEvent(request)) return false;
  navigate(href);
  return true;
}
