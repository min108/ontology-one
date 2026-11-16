import {browserTitle} from './src/i18n/brand';
import {terms} from './src/i18n/zh-CN';
import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react(),{name:'shared-brand-html',transformIndexHtml(html){return html.replace('__ZY_PRODUCT_TITLE__',browserTitle).replace('__ZY_PRODUCT_DESCRIPTION__',terms.labels.product_description)}}],server:{host:'0.0.0.0',port:5173,proxy:{'/api':'http://127.0.0.1:8765'}},build:{assetsDir:'static',chunkSizeWarningLimit:1200}});
