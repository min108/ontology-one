import { describe, expect, it } from 'vitest';
import { browserSummary } from './browserSummary';
import { terms } from '../../i18n/zh-CN';

describe('login device summary', () => {
  it.each([
    ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0 Safari/537.36 Edg/141.0', 'Edge · Windows'],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Version/17.0 Safari/605.1.15', 'Safari · macOS'],
    [
      'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1.15 CriOS/141.0 Safari/604.1',
      'Chrome · iOS',
    ],
    ['Mozilla/5.0 (Linux; Android 14) Chrome/141.0 Mobile Safari/537.36', 'Chrome · Android'],
    ['Mozilla/5.0 (X11; Linux x86_64) Firefox/141.0', 'Firefox · Linux'],
  ])('uses family names without leaking the raw identifier', (agent, expected) => {
    expect(browserSummary(agent)).toBe(expected);
    expect(browserSummary(agent)).not.toContain('Mozilla');
    expect(browserSummary(agent)).not.toContain('141.0');
  });
  it('does not echo arbitrary device identifiers', () => {
    expect(browserSummary('{"session_id":"0123456789abcdef0123456789abcdef"}')).toBe(
      `${terms.labels.r3_admin_browser_unknown} · ${terms.labels.r3_admin_os_unknown}`,
    );
  });
});
