import { terms } from '../../i18n/zh-CN';

/** Display only a browser and OS family; retain the raw identifier in technical details. */
export function browserSummary(agent: string): string {
  const browser = /Edg(?:e|A|iOS)?\//i.test(agent)
    ? 'Edge'
    : /(?:OPR|Opera)\//i.test(agent)
      ? 'Opera'
      : /(?:Firefox|FxiOS)\//i.test(agent)
        ? 'Firefox'
        : /(?:Chrome|CriOS)\//i.test(agent)
          ? 'Chrome'
          : /Safari\//i.test(agent)
            ? 'Safari'
            : terms.labels.r3_admin_browser_unknown;
  const os = /Android/i.test(agent)
    ? 'Android'
    : /(?:iPhone|iPad|iPod)/i.test(agent)
      ? 'iOS'
      : /Windows/i.test(agent)
        ? 'Windows'
        : /(?:Macintosh|Mac OS X)/i.test(agent)
          ? 'macOS'
          : /CrOS/i.test(agent)
            ? 'ChromeOS'
            : /Linux/i.test(agent)
              ? 'Linux'
              : terms.labels.r3_admin_os_unknown;
  return `${browser} · ${os}`;
}
