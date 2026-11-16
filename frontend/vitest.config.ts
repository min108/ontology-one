import { fileURLToPath, URL } from 'node:url';
import { legacyCssPlugin } from './build/legacy-css.mjs';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  resolve: { alias: { '@legacy': fileURLToPath(new URL('./src/legacy', import.meta.url)) } },
  css: { postcss: { plugins: [legacyCssPlugin()] } },
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/design/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: true,
  },
});
