import js from "@eslint/js";
import globals from "globals";

export default [
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
];
