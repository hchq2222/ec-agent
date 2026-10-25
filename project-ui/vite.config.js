import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  base:'/project-ui/',
  plugins:[react()],
  build:{outDir:'../apps/api/static/project-ui',emptyOutDir:true},
  server:{proxy:{'/api':'http://127.0.0.1:8000'}},
});
