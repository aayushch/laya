// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import {
	getEngineUrl,
	getEngineWsUrl,
	getEngineToken,
	setEngineToken,
	waitForEngineToken,
	CODING_AGENTS,
	DEFAULT_AGENT_PATHS,
	AGENT_BINARY_NAMES,
	agentLabel
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

describe('engine auth token helpers', () => {
	it('returns empty string when no token configured', () => {
		setEngineToken(null);
		const win = ((globalThis as any).window ??= {});
		delete win.__LAYA_ENGINE_TOKEN__;
		delete win.__LAYA_CONFIG__;
		expect(getEngineToken()).toBe('');
	});

	it('sets and retrieves engine token', () => {
		setEngineToken('test_token_xyz');
		expect(getEngineToken()).toBe('test_token_xyz');
		setEngineToken(null);
	});

	it('reads from window.__LAYA_CONFIG__', () => {
		setEngineToken(null);
		const win = ((globalThis as any).window ??= {});
		delete win.__LAYA_ENGINE_TOKEN__;
		win.__LAYA_CONFIG__ = { token: 'injected_from_config' };
		expect(getEngineToken()).toBe('injected_from_config');
		delete win.__LAYA_CONFIG__;
		setEngineToken(null);
	});

	it('prioritizes window.__LAYA_ENGINE_TOKEN__ over window.__LAYA_CONFIG__', () => {
		setEngineToken(null);
		const win = ((globalThis as any).window ??= {});
		win.__LAYA_CONFIG__ = { token: 'config_token' };
		win.__LAYA_ENGINE_TOKEN__ = 'window_token';
		expect(getEngineToken()).toBe('window_token');
		delete win.__LAYA_ENGINE_TOKEN__;
		delete win.__LAYA_CONFIG__;
		setEngineToken(null);
	});

	it('waitForEngineToken resolves immediately when token exists', async () => {
		setEngineToken('ready_token');
		const tok = await waitForEngineToken({ timeoutMs: 100 });
		expect(tok).toBe('ready_token');
		setEngineToken(null);
	});

	it('waitForEngineToken resolves after delayed injection', async () => {
		setEngineToken(null);
		setTimeout(() => {
			setEngineToken('delayed_token');
		}, 30);
		const tok = await waitForEngineToken({ intervalMs: 10, timeoutMs: 200 });
		expect(tok).toBe('delayed_token');
		setEngineToken(null);
	});

	it('waitForEngineToken rejects on timeout with clear message', async () => {
		setEngineToken(null);
		await expect(waitForEngineToken({ intervalMs: 10, timeoutMs: 50 })).rejects.toThrow(
			/Engine authentication token was not available within 50ms/
		);
	});
});

describe('coding-agent registry', () => {
	it('offers "none" plus the five CLI agents', () => {
		const values = CODING_AGENTS.map((a) => a.value);
		expect(values).toEqual(['none', 'claude_code', 'gemini_cli', 'codex_cli', 'pi_cli', 'cursor_cli']);
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
			pi_cli: 'pi',
			cursor_cli: 'agent'
		});
		// Every non-"none" agent has a binary name.
		for (const a of CODING_AGENTS) {
			if (a.value === 'none') continue;
			expect(AGENT_BINARY_NAMES[a.value]).toBeTruthy();
		}
	});

	it('labels agent ids for display', () => {
		expect(agentLabel('cursor_cli')).toBe('Cursor Agent');
		expect(agentLabel('claude_code')).toBe('Claude Code');
		expect(agentLabel('')).toBe('Default');
		expect(agentLabel(null, 'unset')).toBe('unset');
		expect(agentLabel('mystery_cli')).toBe('mystery_cli');
	});
});
