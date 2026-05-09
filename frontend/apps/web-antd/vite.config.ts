import { defineConfig } from '@vben/vite-config';

export default defineConfig(async () => {
  return {
    application: {},
    vite: {
      server: {
        proxy: {
          '/admin-api': {
            changeOrigin: true,
            rewrite: (path) => path.replace(/^\/admin-api/, ''),
            // 真实管理后端代理目标地址（独立 AiceMindServer 后端）
            target: 'http://localhost:5010/admin-api',
            ws: true,
          },
        },
      },
    },
  };
});
