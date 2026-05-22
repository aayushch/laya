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
];

export const DEFAULT_AGENT_PATHS: Record<string, string> = Object.fromEntries(
	CODING_AGENTS.filter((a) => a.value !== 'none').map((a) => [a.value, ''])
);

export const AGENT_BINARY_NAMES: Record<string, string> = {
	claude_code: 'claude',
	gemini_cli: 'gemini',
	codex_cli: 'codex',
	pi_cli: 'pi',
};
