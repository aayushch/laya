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
