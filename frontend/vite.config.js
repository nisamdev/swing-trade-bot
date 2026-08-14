import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The browser only ever talks to this dev server. Anything starting with /api
// is forwarded to the Python side, so there is no cross-origin request to
// configure and no API URL to paste anywhere.
const API_TARGET = process.env.API_TARGET || 'http://localhost:8020'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    host: true,
    // Vite rejects unknown Host headers by default, which blocks reaching the
    // dev server by container name or by the machine's LAN address. This is a
    // local, single-user app, so allow any host rather than making people edit
    // this file to open the page from their phone.
    allowedHosts: true,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
    },
  },
})
