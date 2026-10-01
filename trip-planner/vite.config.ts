/// <reference types="vitest/config" />
import { defineConfig, loadEnv, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import type { IncomingMessage, ServerResponse } from "http";

const root = import.meta.dirname;

/**
 * Serves /api/* from server/index.ts during development, so `npm run dev` runs
 * the whole app in one process. Vercel runs the same handler, bundled by
 * scripts/build-vercel.ts.
 */
function devApi(): Plugin {
  return {
    name: "dev-api",
    configureServer(server) {
      Object.assign(process.env, loadEnv("development", root, ""));
      server.middlewares.use(async (req: IncomingMessage, res: ServerResponse, next) => {
        if (!req.url?.startsWith("/api/")) return next();
        try {
          const { handle } = await server.ssrLoadModule(path.resolve(root, "server/index.ts"));
          const { serveNode } = await server.ssrLoadModule(path.resolve(root, "server/node-adapter.ts"));
          await serveNode(handle, req, res);
        } catch (e) {
          next(e);
        }
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), devApi()],
  resolve: {
    alias: {
      "@": path.resolve(root, "client", "src"),
      "@planner": path.resolve(root, "planner", "index.ts"),
    },
  },
  root: path.resolve(root, "client"),
  build: {
    outDir: path.resolve(root, "dist"),
    emptyOutDir: true,
  },
  test: {
    root,
    include: ["tests/**/*.test.ts"],
  },
});
