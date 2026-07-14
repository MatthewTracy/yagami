import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (
            ["react", "react-dom", "scheduler"].some((dependency) =>
              id.includes(`/node_modules/${dependency}/`),
            )
          ) {
            return "react-vendor";
          }

          if (
            [
              "highlight.js",
              "react-markdown",
              "rehype-highlight",
              "remark-gfm",
            ].some((dependency) =>
              id.includes(`/node_modules/${dependency}/`),
            )
          ) {
            return "markdown-vendor";
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/api": { target: "http://localhost:8000" },
    },
  },
});
