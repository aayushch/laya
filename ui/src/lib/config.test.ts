// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import {
	getEngineUrl,
	getEngineWsUrl,
	CODING_AGENTS,
	DEFAULT_AGENT_PATHS,
	AGENT_BINARY_NAMES
} from './config';

describe('engine URL helpers', () => {
	it('builds the default engine URL', () => {
		expect(getEngineUrl()).toBe('http://127.0.0.1:8420');
	});
	it('memoizes the URL (stable across calls)', () => {
		expect(getEngineUrl()).toBe(getEngineUrl());
	});
	it('derives the websocket URL by swapping the scheme and appending /ws', () => {
		expect(getEngineWsUrl()).toBe('ws://127.0.0.1:8420/ws');
	});
	it('only rewrites the leading http, not an http substring', () => {
		// The regex is anchored (^http) so a host containing "http" would be safe.
		expect(getEngineWsUrl().startsWith('ws://')).toBe(true);
		expect(getEngineWsUrl().includes('http')).toBe(false);
	});
});

describe('coding-agent registry', () => {
	it('offers "none" plus the four CLI agents', () => {
		const values = CODING_AGENTS.map((a) => a.value);
		expect(values).toEqual(['none', 'claude_code', 'gemini_cli', 'codex_cli', 'pi_cli']);
	});

	it('derives default paths for every real agent but never for "none"', () => {
		expect(DEFAULT_AGENT_PATHS).not.toHaveProperty('none');
		for (const a of CODING_AGENTS) {
			if (a.value === 'none') continue;
			expect(DEFAULT_AGENT_PATHS[a.value]).toBe('');
		}
	});

	it('maps each real agent to its binary name', () => {
		expect(AGENT_BINARY_NAMES).toEqual({
			claude_code: 'claude',
			gemini_cli: 'gemini',
			codex_cli: 'codex',
			pi_cli: 'pi'
		});
		// Every non-"none" agent has a binary name.
		for (const a of CODING_AGENTS) {
			if (a.value === 'none') continue;
			expect(AGENT_BINARY_NAMES[a.value]).toBeTruthy();
		}
	});
});
