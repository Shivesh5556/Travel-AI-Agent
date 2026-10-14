import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Anything starting with /api gets forwarded to the FastAPI server on port
// 8000. That way the browser thinks everything is on one origin and we do
// not fight CORS during development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
