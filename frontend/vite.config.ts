import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const controlPlaneApi = `http://127.0.0.1:${process.env.CONTROL_PLANE_API_PORT ?? '8001'}`

export default defineConfig({
  plugins: [react()],
  cacheDir: '../.local/vite-cache',
  server: {
    proxy: {
      '/api/v1/auth': controlPlaneApi,
      '/api/v1/workspaces': controlPlaneApi,
      '/api/v1/invitations': controlPlaneApi,
      '/api/v1/runners': controlPlaneApi,
      '/api/v1/me': controlPlaneApi,
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: '../backend/app/static',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        support: 'index.html',
        controlPlane: 'control-plane.html',
      },
    },
  },
})
