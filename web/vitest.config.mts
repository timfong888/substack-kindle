import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // convex-test recommends edge-runtime: closest to the Convex JS runtime.
    environment: "edge-runtime",
    server: { deps: { inline: ["convex-test"] } },
    include: ["convex/**/*.test.ts"],
  },
});
