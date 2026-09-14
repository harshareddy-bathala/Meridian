// The dashboard's lint rules, and the limits the Python side already enforces.
//
// D-081: ruff and mypy stop at the language boundary, so the size and complexity
// limits in pyproject.toml are restated here with the same numbers. Otherwise the
// second codebase grows its own standards by default.
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/", "node_modules/"] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  reactHooks.configs.flat.recommended,
  {
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      // The module cap the CI step applies to Python (CLAUDE.local.md section 2).
      // Blank lines and comments count, as they do for `wc -l`.
      "max-lines": ["error", { max: 400, skipBlankLines: false, skipComments: false }],
      // [tool.ruff.lint.mccabe] max-complexity and [tool.ruff.lint.pylint].
      complexity: ["error", 8],
      "max-params": ["error", 5],
      "max-statements": ["error", 30],
      // Python has no function-length number, only the 30-statement cap above,
      // and JSX spends lines that are not statements. 60 is the one limit here
      // without a Python twin, set so a component still fits on a screen.
      "max-lines-per-function": [
        "error",
        { max: 60, skipBlankLines: true, skipComments: true },
      ],
    },
  },
  {
    // The config files are plain modules outside the type-checked source.
    files: ["eslint.config.js"],
    ...tseslint.configs.disableTypeChecked,
  },
);
