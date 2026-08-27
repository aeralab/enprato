import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import pkg from "./package.json";

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    allowedHosts: true,
    proxy: {
      "/api": {
        target: "https://127.0.0.1:18787",
        secure: false,
        timeout: 600000,
        proxyTimeout: 600000,
        configure(proxy) {
          proxy.on("proxyRes", (proxyRes, req) => {
            if (req.url?.includes("/api/session/") && (req.url.includes("/video") || req.url.includes("/audio"))) {
              proxyRes.headers["accept-ranges"] = "bytes";
            }
          });
        },
      },
    },
  },
});
