import { useEffect, useState } from 'react';
import { UploadCloud } from 'lucide-react';
import { terms } from '../i18n/zh-CN';
import styles from './DropOverlay.module.css';
export function DropOverlay({
  enabled = true,
  onFiles,
}: {
  enabled?: boolean;
  onFiles: (files: File[]) => void;
}) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let depth = 0;
    const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files');
    const enter = (event: DragEvent) => {
      if (hasFiles(event) && !event.defaultPrevented) {
        event.preventDefault();
        depth += 1;
        setVisible(true);
      }
    };
    const leave = (event: DragEvent) => {
      if (hasFiles(event)) {
        depth = Math.max(0, depth - 1);
        if (!depth) setVisible(false);
      }
    };
    const over = (event: DragEvent) => {
      if (hasFiles(event)) event.preventDefault();
    };
    const drop = (event: DragEvent) => {
      if (event.defaultPrevented) {
        depth = 0;
        setVisible(false);
        return;
      }
      if (hasFiles(event)) {
        event.preventDefault();
        depth = 0;
        setVisible(false);
        const files = Array.from(event.dataTransfer?.files ?? []);
        if (files.length) onFiles(files);
      }
    };
    const reset = () => {
      depth = 0;
      setVisible(false);
    };
    document.addEventListener('dragenter', enter);
    document.addEventListener('dragleave', leave);
    document.addEventListener('dragover', over);
    document.addEventListener('drop', drop);
    window.addEventListener('blur', reset);
    return () => {
      document.removeEventListener('dragenter', enter);
      document.removeEventListener('dragleave', leave);
      document.removeEventListener('dragover', over);
      document.removeEventListener('drop', drop);
      window.removeEventListener('blur', reset);
    };
  }, [enabled, onFiles]);
  if (!enabled || !visible) return null;
  return (
    <div className={styles.overlay} role="status">
      <div className={styles.panel}>
        <UploadCloud aria-hidden="true" />
        <h2>{terms.labels.drop_title}</h2>
        <p>{terms.labels.drop_description}</p>
      </div>
    </div>
  );
}
