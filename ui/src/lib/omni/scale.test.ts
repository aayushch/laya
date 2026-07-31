// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// Static guard for --om-scale, the Omni board's type-scale multiplier.
//
// The board sizes every role as `calc(Npx * var(--om-scale))`. If --om-scale
// ever carries a unit, each of those products is px×px — not a length — so the
// declaration is invalid and EVERY size on the board silently falls back to the
// inherited ~16px. Nothing throws, nothing type-errors, and the Text Size
// setting appears to do nothing; it just renders enormous. That shipped once
// (`calc(var(--laya-font-base) / 13)` resolves to 1px, not 1), so it is pinned
// here rather than left to the eye.

import { describe, it, expect } from 'vitest';
// The stylesheet is read from disk, not imported: vitest stubs CSS modules
// (`css: false`), so `?raw` and `?inline` both come back as an empty string and
// every assertion below would pass vacuously.
// @ts-expect-error - @types/node isn't a dependency; this runs under vitest (node).
// If it ever is installed, svelte-check flags this directive as unused — delete it.
import { readFileSync } from 'node:fs';
import LAYOUT from '../../routes/+layout.svelte?raw';

// Relative to the vitest root (ui/).
const APP_CSS: string = readFileSync('src/app.css', 'utf8');
// Comments in this block describe the failure mode using the same syntax, so
// they have to come out before scanning for real declarations.
const APP_CSS_CODE = APP_CSS.replace(/\/\*[\s\S]*?\*\//g, '');

describe('--om-scale', () => {
	it('is declared as a unitless number in app.css', () => {
		const declarations = [...APP_CSS_CODE.matchAll(/--om-scale:\s*([^;]+);/g)].map((m) =>
			m[1].trim()
		);
		expect(declarations.length).toBeGreaterThan(0);
		for (const value of declarations) {
			expect(value).toMatch(/^[\d.]+$/);
		}
	});

	it('is never derived in CSS from a length', () => {
		// calc(var(--laya-font-base) / 13) yields 1px — the exact regression.
		expect(APP_CSS_CODE).not.toMatch(/--om-scale:\s*calc\(/);
	});

	it('is kept in step with the Text Size setting by the layout', () => {
		expect(LAYOUT).toContain("setProperty('--om-scale'");
		// Set as a bare ratio, not a px string.
		expect(LAYOUT).toMatch(/setProperty\('--om-scale',\s*String\(\$fontScale\s*\/\s*13\)\)/);
	});

	it('multiplies a px literal in every board type rule', () => {
		const uses = [...APP_CSS_CODE.matchAll(/calc\(([^)]*?)var\(--om-scale\)\)/g)].map((m) =>
			m[1].trim()
		);
		expect(uses.length).toBeGreaterThan(10);
		for (const expr of uses) {
			// Always `<number>px * ` — never a division, never a bare multiplier.
			expect(expr).toMatch(/^[\d.]+px\s*\*\s*$/);
		}
	});
});
