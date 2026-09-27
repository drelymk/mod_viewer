import js from '@eslint/js';
import globals from 'globals';

export default [
  { ignores: ['src/web/lib/**'] },
  {
    ...js.configs.recommended,
    files: ['src/web/js/**/*.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.browser,
    },
  },
];
