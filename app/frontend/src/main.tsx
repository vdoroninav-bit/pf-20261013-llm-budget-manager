import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import 'virtual:bm-tokens.css';
import { App } from './App';

const root = document.getElementById('root');
if (!root) throw new Error('В index.html нет #root');

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
