import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  // process.env for Docker env vars, loadEnv for .env files, then default
  const apiUrl = process.env.VITE_API_URL || env.VITE_API_URL || 'http://localhost:9000'
  const wsUrl = process.env.VITE_WS_URL || env.VITE_WS_URL || 'ws://localhost:9000'

  return {
    plugins: [react()],

    // Build configuration
    build: {
      outDir: 'dist',
      sourcemap: mode !== 'production',
      minify: 'terser',
      terserOptions: {
        compress: {
          drop_console: false,  // Temporarily enabled for debugging
          drop_debugger: mode === 'production'
        }
      },
      rollupOptions: {
        output: {
          manualChunks: {
            vendor: ['react', 'react-dom', 'react-router-dom'],
            charts: ['vega', 'vega-lite', 'react-vega'],
            network: ['vis-data', 'vis-network']
          }
        }
      }
    },

    // Development server
    server: {
      port: 3000,
      host: '0.0.0.0',
      allowedHosts: true,
      proxy: {
        '/api': {
          target: apiUrl,
          changeOrigin: true,
        },
        '/uploads': {
          target: apiUrl,
          changeOrigin: true,
        },
        '/ws': {
          target: wsUrl,
          ws: true,
          changeOrigin: true,
        }
      }
    },

    // Preview server (for production build testing)
    preview: {
      port: 3000,
      host: '0.0.0.0'
    },

    // Environment variable prefix
    envPrefix: 'VITE_',

    // Define global constants
    define: {
      __APP_VERSION__: JSON.stringify(process.env.npm_package_version || '0.3.0')
    }
  }
})
