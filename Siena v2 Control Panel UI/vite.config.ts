import { defineConfig } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import pkg from './package.json'


function figmaAssetResolver() {
  return {
    name: 'figma-asset-resolver',
    resolveId(id) {
      if (id.startsWith('figma:asset/')) {
        const filename = id.replace('figma:asset/', '')
        return path.resolve(__dirname, 'src/assets', filename)
      }
    },
  }
}

export default defineConfig({
  base: './',
  // Real app version for Settings > Developer > About (was a hardcoded,
  // stale-looking "0.9.4-beta" placeholder before) — sourced from
  // package.json so it can't drift from what's actually installed.
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  plugins: [
    figmaAssetResolver(),
    // The React and Tailwind plugins are both required for Make, even if
    // Tailwind is not being actively used – do not remove them
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      // Alias @ to the src directory
      '@': path.resolve(__dirname, './src'),
    },
  },

  // Pinned to the IPv4 loopback address on purpose: Electron's dev-mode
  // window (see electron/main.cjs, SIENA_DEV_SERVER_URL) loads
  // http://127.0.0.1:5173 explicitly, and the backend's CORS allowlist
  // (api/server.py) only permits that exact origin — plain "localhost" can
  // resolve to the IPv6 ::1 loopback on Windows/Node, which Vite would then
  // bind instead, and the two would fail to connect.
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },

  // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
  assetsInclude: ['**/*.svg', '**/*.csv'],
})
