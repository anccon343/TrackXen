import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// The built UI is served by the TrackXen engine (engine/trackxen/core/server.py).
// During development, `npm run dev` proxies the API and WebSocket to a running engine.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: "../engine/trackxen/core/static/app",
    emptyOutDir: true,
    assetsDir: "assets",
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8090",
      "/ws": { target: "ws://127.0.0.1:8090", ws: true },
    },
  },
});
