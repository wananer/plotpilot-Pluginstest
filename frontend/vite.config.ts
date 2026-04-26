import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const DEV_FRONTEND_PORT = Number(process.env.PLOTPILOT_FRONTEND_PORT || 3000)
const DEV_API_TARGET = process.env.PLOTPILOT_API_TARGET || 'http://127.0.0.1:8005'
const DEV_PLUGIN_TARGET = process.env.PLOTPILOT_PLUGIN_TARGET || DEV_API_TARGET

// https://vite.dev/config/
export default defineConfig({
  build: {
    // 大型 SPA 常见体积；需要更细拆分时再改 code-splitting，而非被默认 500k 告警刷屏
    chunkSizeWarningLimit: 1200,
  },
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: DEV_FRONTEND_PORT,
    host: '127.0.0.1',
    proxy: {
      '/plugins': {
        target: DEV_PLUGIN_TARGET,
        changeOrigin: true,
        rewrite: (path) => path,
      },
      // 代理到后端服务器（默认 8005 端口）
      '/api': {
        target: DEV_API_TARGET,
        changeOrigin: true,
        ws: true,
        // SSE 长连接，避免代理过早断开
        timeout: 0,
        // 不要重写路径
        rewrite: (path) => path,
      },
    },
  },
})
