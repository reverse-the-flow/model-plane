import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backendTarget = process.env.VITE_MODEL_PLANE_PROXY_TARGET || "http://127.0.0.1:19110";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 19111,
    proxy: {
      "/model-plane-api": {
        target: backendTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/model-plane-api/, ""),
      },
    },
  },
});
