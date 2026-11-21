import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (/\/node_modules\/(echarts|zrender)\//.test(id)) return 'sales-charts'
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/export': 'http://127.0.0.1:8080',
      '/templates': 'http://127.0.0.1:8080',
    },
  },
})
