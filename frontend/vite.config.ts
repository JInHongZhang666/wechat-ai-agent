import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8010',
      '/health': 'http://127.0.0.1:8010',
    },
  },
  build: {
    // This is the frozen legacy workbench. Keep its build output local so a
    // build can never erase the source of the currently served AIHOT UI.
    outDir: 'dist',
    emptyOutDir: true,
  },
});
