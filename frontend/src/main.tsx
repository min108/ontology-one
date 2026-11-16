import React, { lazy, Suspense } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './app/App';
import '@legacy/styles.css';
import { browserTitle } from './i18n/brand';

document.title = browserTitle;
const Gallery = import.meta.env.DEV ? lazy(() => import('./features/dev/gallery/Gallery')) : null;
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      {Gallery && window.location.pathname === '/dev/gallery' ? (
        <Suspense fallback={null}>
          <Gallery />
        </Suspense>
      ) : (
        <App />
      )}
    </BrowserRouter>
  </React.StrictMode>,
);
