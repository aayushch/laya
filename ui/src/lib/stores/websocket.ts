import { writable } from 'svelte/store';
import type { WsMessage } from '$lib/api/types';

export type WsStatus = 'connecting' | 'connected' | 'disconnected';

export const wsStatus = writable<WsStatus>('disconnected');
export const lastMessage = writable<WsMessage | null>(null);

const ENGINE_WS_URL = 'ws://127.0.0.1:8420/ws';
let socket: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelay = 1000;

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
			lastMessage.set(msg);
		} catch {
			// ignore non-JSON messages
		}
	};

	socket.onclose = () => {
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
	socket?.close();
	socket = null;
	wsStatus.set('disconnected');
}

export function sendMessage(msg: Record<string, unknown>) {
	if (socket?.readyState === WebSocket.OPEN) {
		socket.send(JSON.stringify(msg));
	}
}
