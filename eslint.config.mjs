import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import globals from "globals";

export default defineConfig([
  {
    ignores: ["node_modules/**", ".venv/**"],
  },
  js.configs.recommended,
  {
    files: ["MMM-AnkerDeviceStatus.js"],
    languageOptions: {
      globals: {
        ...globals.browser,
        Module: "readonly",
      },
    },
  },
  {
    files: ["node_helper.js", "tests/**/*.js"],
    languageOptions: {
      globals: globals.node,
    },
  },
]);
