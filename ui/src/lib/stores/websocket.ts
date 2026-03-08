import { writable } from 'svelte/store';
import type { WsMessage } from '$lib/api/types';

export type WsStatus = 'connecting' | 'connected' | 'disconnected';

export const wsStatus = writable<WsStatus>('disconnected');
export const lastMessage = writable<WsMessage | null>(null);

const ENGINE_WS_URL = 'ws://127.0.0.1:8420/ws';
let socket: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelay = 1000;

// Non-reactive incoming message buffer.
// Messages are pushed here synchronously in onmessage and drained
// one-at-a-time via setTimeout(0) so the browser's task scheduler
// can interleave rendering and user input between each message.
const _msgQueue: WsMessage[] = [];
let _draining = false;

function _scheduleNext(): void {
	if (_msgQueue.length === 0) {
		_draining = false;
		return;
	}
	_draining = true;
	setTimeout(() => {
		const msg = _msgQueue.shift();
		if (msg) lastMessage.set(msg);
		_scheduleNext();
	}, 0);
}

function connect() {
	if (socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) {
		return;
	}

	wsStatus.set('connecting');
	socket = new WebSocket(ENGINE_WS_URL);

	socket.onopen = () => {
		wsStatus.set('connected');
		reconnectDelay = 1000; // reset backoff
	};

	socket.onmessage = (event) => {
		try {
			const msg: WsMessage = JSON.parse(event.data);
			_msgQueue.push(msg);
			if (!_draining) _scheduleNext();
		} catch {
			// ignore non-JSON messages
		}
	};

	socket.onclose = () => {
		// Clear stale queued messages before reconnecting
		_msgQueue.length = 0;
		_draining = false;
		wsStatus.set('disconnected');
		scheduleReconnect();
	};

	socket.onerror = () => {
		socket?.close();
	};
}

function scheduleReconnect() {
	if (reconnectTimer) clearTimeout(reconnectTimer);
	reconnectTimer = setTimeout(() => {
		reconnectDelay = Math.min(reconnectDelay * 2, 10000); // cap at 10s
		connect();
	}, reconnectDelay);
}

export function initWebSocket() {
	connect();
}

export function closeWebSocket() {
	if (reconnectTimer) clearTimeout(reconnectTimer);
	reconnectTimer = null;
	_msgQueue.length = 0;
	_draining = false;
	socket?.close();
	socket = null;
	wsStatus.set('disconnected');
}

export function sendMessage(msg: Record<string, unknown>) {
	if (socket?.readyState === WebSocket.OPEN) {
		socket.send(JSON.stringify(msg));
	}
}
