import { App } from 'antd';
import { useEffect } from 'react';
import { terms } from '../i18n/zh-CN';
import type { WorkspaceGuardSnapshot } from '../state/workspace-guard';

type Confirmation = (snapshot: WorkspaceGuardSnapshot) => Promise<boolean>;
interface WorkspaceConfirmationProps {
  register: (confirm: Confirmation) => () => void;
}

export function WorkspaceConfirmation({ register }: WorkspaceConfirmationProps) {
  const { modal } = App.useApp();
  useEffect(() => {
    let dismiss: (() => void) | undefined;
    const unsubscribe = register(
      (snapshot) =>
        new Promise<boolean>((resolve) => {
          const instance = modal.confirm({
            title: terms.labels.shell_switch_title,
            content: (
              <div>
                {snapshot.dirtyCount > 0 && <p>{terms.labels.shell_switch_dirty}</p>}
                {snapshot.uploadCount > 0 && <p>{terms.labels.shell_switch_upload}</p>}
                {snapshot.storedDraftCount > 0 && <p>{terms.labels.shell_switch_stored}</p>}
                <p>{terms.labels.shell_switch_continue}</p>
              </div>
            ),
            okText: terms.actions.shell_switch_confirm,
            cancelText: terms.actions.cancel,
            okButtonProps: { autoInsertSpace: false },
            cancelButtonProps: { autoInsertSpace: false },
            maskClosable: false,
            onOk: () => {
              dismiss = undefined;
              resolve(true);
            },
            onCancel: () => {
              dismiss = undefined;
              resolve(false);
            },
          });
          dismiss = () => {
            resolve(false);
            instance.destroy();
          };
        }),
    );
    return () => {
      unsubscribe();
      dismiss?.();
    };
  }, [register, modal]);
  return null;
}
