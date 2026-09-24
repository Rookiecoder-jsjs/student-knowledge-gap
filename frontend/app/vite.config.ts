import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 开发期把 /api 代理到后端 FastAPI（8000），前端代码统一请求 /api/*。
// 会话网关（AI 教研员）走同源 /rpc 与 /threads/*，dev 代理到网关 8100——
// 网关不配 CORS，浏览器必须同源访问（生产由 frontend/app/nginx.conf 同源反代）。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.SC_DEV_API_URL || "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
      "/rpc": { target: "http://127.0.0.1:8100", changeOrigin: true },
      "/threads": { target: "http://127.0.0.1:8100", changeOrigin: true },
    },
  },
});
