import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["tests/js/**/*.test.js"],
    coverage: {
      provider: "v8",
      // "all" so an untested file is reported as 0% rather than omitted,
      // which would silently flatter the number.
      all: true,
      include: ["static/js/**/*.js"],
      reporter: ["text", "lcov"],
      reportsDirectory: "coverage-js"
    }
  }
});
