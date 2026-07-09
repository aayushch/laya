import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

// Standalone config for pure-logic unit tests (no SvelteKit plugin needed — the
// modules under test import only types from $lib, which esbuild elides). Kept
// separate from vite.config.ts so tests don't pull in the full Kit/Tailwind
// pipeline.
export default defineConfig({
	resolve: {
		alias: {
			$lib: fileURLToPath(new URL('./src/lib', import.meta.url)),
		},
	},
	test: {
		include: ['src/**/*.{test,spec}.ts'],
		environment: 'node',
	},
});
