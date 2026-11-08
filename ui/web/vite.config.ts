import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时（npm run dev）把接口转给 python main.py ui 启动的后端
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/ws": { target: "ws://127.0.0.1:8765", ws: true },
    },
  },
});
