import { terms } from '../i18n/zh-CN';
import styles from './Foundation.module.css';
export function SourceViewer({
  text,
  quote,
  start,
  end,
}: {
  text: string;
  quote: string;
  start?: number;
  end?: number;
}) {
  const characters = Array.from(text);
  const index = start ?? (quote ? Array.from(text.slice(0, text.indexOf(quote))).length : -1);
  const finish = end ?? index + Array.from(quote).length;
  const exact =
    Boolean(quote) && index >= 0 && finish > index && characters.slice(index, finish).join('') === quote;
  return (
    <div className={styles.source} data-source-viewer>
      <blockquote>
        {exact ? (
          <>
            {characters.slice(0, index).join('')}
            <mark>{characters.slice(index, finish).join('')}</mark>
            {characters.slice(finish).join('')}
          </>
        ) : (
          text
        )}
      </blockquote>
      {!exact && quote && <p role="status">{terms.labels.r3_ui_quote_unmatched}</p>}
    </div>
  );
}
