import { useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

export type AddressPatch = Record<string, string | number | readonly string[] | null | undefined>;

/** Merge only owned fields; preserve other page filters and object context. */
export function patchAddressSearch(search: string, patch: AddressPatch): string {
  const params = new URLSearchParams(search);
  for (const [key, value] of Object.entries(patch)) {
    params.delete(key);
    if (value === null || value === undefined || value === '') continue;
    for (const item of Array.isArray(value) ? value : [value]) params.append(key, String(item));
  }
  const encoded = params.toString();
  return encoded ? '?' + encoded : '';
}

export function addressPage(value: string | null, fallback = 1): number {
  if (!value || !/^[1-9]\d*$/.test(value)) return fallback;
  const page = Number(value);
  return Number.isSafeInteger(page) ? page : fallback;
}

/** The URL is the source of truth, including refresh and browser back/forward. */
export function useAddressState() {
  const location = useLocation();
  const navigate = useNavigate();
  const observed = useRef(location.search);
  const pending = useRef(location.search);
  if (observed.current !== location.search) {
    observed.current = location.search;
    pending.current = location.search;
  }
  return {
    params: new URLSearchParams(location.search),
    update: (patch: AddressPatch, options: { replace?: boolean } = {}) => {
      pending.current = patchAddressSearch(pending.current, patch);
      void navigate(
        {
          pathname: location.pathname,
          search: pending.current,
          hash: location.hash,
        },
        { replace: options.replace ?? true },
      );
    },
  };
}
