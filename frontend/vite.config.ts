import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "../app/static",
    emptyOutDir: false,
    sourcemap: false,
    minify: false,
    lib: {
      entry: "src/main.ts",
      formats: ["es"],
      fileName: () => "main.js",
    },
    rollupOptions: {
      external: ["three"],
    },
  },
});
