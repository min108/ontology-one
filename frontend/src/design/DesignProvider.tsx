import { App, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import type { ReactNode } from 'react';
import { theme } from './theme';
import './global.css';

export function DesignProvider({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider theme={theme} locale={zhCN}>
      <App className="zy-root">{children}</App>
    </ConfigProvider>
  );
}
