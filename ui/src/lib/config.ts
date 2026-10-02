// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

/**
 * Centralized engine URL configuration.
 *
 * All modules that need to reach the engine should import from here
 * instead of hardcoding 127.0.0.1:8420.
 */

const DEFAULT_ENGINE_HOST = '127.0.0.1';
const DEFAULT_ENGINE_PORT = '8420';

let _engineUrl: string | null = null;

export function getEngineUrl(): string {
	if (_engineUrl) return _engineUrl;
	_engineUrl = `http://${DEFAULT_ENGINE_HOST}:${DEFAULT_ENGINE_PORT}`;
	return _engineUrl;
}

export function getEngineWsUrl(): string {
	return getEngineUrl().replace(/^http/, 'ws') + '/ws';
}

/**
 * Engine authentication token resolution order:
 * 1. window.__LAYA_ENGINE_TOKEN__ (injected by Tauri shell at runtime)
 * 2. window.__LAYA_CONFIG__?.token (injected config object)
 * 3. import.meta.env.VITE_LAYA_ENGINE_TOKEN (browser-only Vite dev mode)
 *    To use in browser dev mode:
 *    Add VITE_LAYA_ENGINE_TOKEN=<token> to ui/.env.local (gitignored).
 *    Never commit secrets. In production, tokens are injected by Tauri at runtime.
 * 4. else ""
 */
let _fallbackEngineToken = '';

export function getEngineToken(): string {
	if (typeof window !== 'undefined') {
		const win = window as unknown as {
			__LAYA_ENGINE_TOKEN__?: string;
			__LAYA_CONFIG__?: { token?: string };
		};
		if (win.__LAYA_ENGINE_TOKEN__) {
			return win.__LAYA_ENGINE_TOKEN__;
		}
		if (win.__LAYA_CONFIG__?.token) {
			return win.__LAYA_CONFIG__.token;
		}
	}
	if (import.meta.env?.DEV && import.meta.env.VITE_LAYA_ENGINE_TOKEN) {
		return import.meta.env.VITE_LAYA_ENGINE_TOKEN as string;
	}
	return _fallbackEngineToken;
}

export function setEngineToken(token: string | null): void {
	_fallbackEngineToken = token ?? '';
	if (typeof window !== 'undefined') {
		const win = window as unknown as { __LAYA_ENGINE_TOKEN__?: string | null };
		win.__LAYA_ENGINE_TOKEN__ = token ?? undefined;
	}
}

export interface WaitForEngineTokenOptions {
	intervalMs?: number;
	timeoutMs?: number;
}

/**
 * Resolves when engine authentication token is non-empty.
 * Times out with an informative error if token is not available within timeoutMs.
 */
export async function waitForEngineToken(options?: WaitForEngineTokenOptions): Promise<string> {
	const intervalMs = options?.intervalMs ?? 20;
	const timeoutMs = options?.timeoutMs ?? 5000;

	const token = getEngineToken();
	if (token) return token;

	// In Tauri, attempt to retrieve token directly via command if available
	if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
		try {
			const { invoke } = await import('@tauri-apps/api/core');
			const tauriToken = await invoke<string | null>('get_engine_token');
			if (tauriToken) {
				setEngineToken(tauriToken);
				return tauriToken;
			}
		} catch {
			// Tauri command unavailable or failed, fallback to polling
		}
	}

	return new Promise((resolve, reject) => {
		const startTime = Date.now();
		const interval = setInterval(() => {
			const current = getEngineToken();
			if (current) {
				clearInterval(interval);
				resolve(current);
				return;
			}
			if (Date.now() - startTime >= timeoutMs) {
				clearInterval(interval);
				reject(
					new Error(
						`Engine authentication token was not available within ${timeoutMs}ms. In browser dev mode, set VITE_LAYA_ENGINE_TOKEN in ui/.env.local.`
					)
				);
			}
		}, intervalMs);
	});
}

export interface AgentOption {
	value: string;
	label: string;
	description: string;
}

export const CODING_AGENTS: AgentOption[] = [
	{ value: 'none', label: 'None', description: 'No coding agent — handle code tasks manually' },
	{ value: 'claude_code', label: 'Claude Code', description: 'Anthropic CLI — structured JSON streaming, approval prompts' },
	{ value: 'gemini_cli', label: 'Gemini CLI', description: 'Google CLI — structured JSON output' },
	{ value: 'codex_cli', label: 'Codex CLI', description: 'OpenAI CLI — structured JSON output' },
	{ value: 'pi_cli', label: 'Pi', description: 'Local-first agent — supports Ollama and 15+ providers' },
	{ value: 'cursor_cli', label: 'Cursor Agent', description: 'Cursor CLI — plan / edit / full-access modes, structured JSON streaming' },
];

export const DEFAULT_AGENT_PATHS: Record<string, string> = Object.fromEntries(
	CODING_AGENTS.filter((a) => a.value !== 'none').map((a) => [a.value, ''])
);

export const AGENT_BINARY_NAMES: Record<string, string> = {
	claude_code: 'claude',
	gemini_cli: 'gemini',
	codex_cli: 'codex',
	pi_cli: 'pi',
	cursor_cli: 'agent',
};

/** Human label for a coding-agent id; unknown ids render as-is, empty as `fallback`. */
export function agentLabel(value: string | null | undefined, fallback = 'Default'): string {
	if (!value) return fallback;
	return CODING_AGENTS.find((a) => a.value === value)?.label ?? value;
}
