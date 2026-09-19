// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

// Module-level chat stream handling.
//
// This lives OUTSIDE ChatSidebar so a chat keeps streaming into the stores
// while the sidebar is closed: the engine processes each chat as a detached
// task and broadcasts chunks regardless of what the UI shows, but the old
// in-component $effect died with the component on close — every event emitted
// while the panel was closed was dropped, so a backgrounded chat looked
// aborted when reopened. initChatStream() is called once from the root layout,
// next to initWebSocket().

import { get } from 'svelte/store';
import { lastMessage, wsStatus, type WsStatus } from './websocket';
import {
	chatMessages,
	streamingMessageId,
	activeTools,
	activeConversationId,
	conversations,
	chatSending
} from './chat';
import { engineApi } from '$lib/api/engine';
import type { ChatMessage } from '$lib/api/types';

let initialized = false;

/** Conversation whose stream was in flight when the WS dropped — used to
 *  re-pull its persisted content from the DB once the WS reconnects. */
let interruptedConvId: string | null = null;
let prevWsStatus: WsStatus = 'disconnected';

export function initChatStream(): void {
	// Idempotent: HMR / repeated layout mounts must not double-subscribe, or
	// every stream chunk would be appended twice.
	if (initialized) return;
	initialized = true;
	lastMessage.subscribe((msg) => {
		if (msg) handleChatStreamMessage(msg as unknown as Record<string, unknown> & { type: string });
	});
	wsStatus.subscribe(handleWsStatus);
}

/**
 * Merge a DB message load with an in-flight streaming reply instead of
 * clobbering it. The DB copy of the streaming message lags the live one (the
 * engine flushes partial content on a ~1s throttle), so while its stream is
 * active the store's version wins. Pure — unit-tested in chatStream.test.ts.
 */
export function mergeStreamingIntoLoaded(
	loaded: ChatMessage[],
	live: ChatMessage | undefined,
	conversationId: string | null
): ChatMessage[] {
	if (!live) return loaded;
	// A stream belonging to a different conversation must not leak into this one.
	if (conversationId && live.conversation_id && live.conversation_id !== conversationId) {
		return loaded;
	}
	const idx = loaded.findIndex((m) => m.message_id === live.message_id);
	if (idx >= 0) {
		const merged = [...loaded];
		merged[idx] = live;
		return merged;
	}
	return [...loaded, live];
}

/** Replace chatMessages with a DB load, preserving any in-flight streaming reply. */
export function applyLoadedMessages(conversationId: string | null, loaded: ChatMessage[]): void {
	const streamId = get(streamingMessageId);
	const live = streamId
		? get(chatMessages).find((m) => m.message_id === streamId)
		: undefined;
	chatMessages.set(mergeStreamingIntoLoaded(loaded, live, conversationId));
}

function handleChatStreamMessage(raw: Record<string, unknown> & { type: string }): void {
	switch (raw.type) {
		case 'chat_stream_start': {
			const msgId = raw.message_id as string;
			const convId = raw.conversation_id as string | undefined;
			streamingMessageId.set(msgId);
			activeTools.set([]);
			// Track the conversation if auto-created by backend
			if (convId && !get(activeConversationId)) {
				activeConversationId.set(convId);
			}
			// Add the placeholder — but only into the conversation it belongs to.
			// If the user switched to another conversation between send and start,
			// the stream state stays global while the visible list is left alone;
			// the reply is picked up from the DB when its conversation is reopened.
			if (!convId || get(activeConversationId) === convId) {
				const placeholder: ChatMessage = {
					message_id: msgId,
					timestamp: new Date().toISOString(),
					role: 'assistant',
					content: '',
					referenced_cards: [],
					referenced_events: [],
					conversation_id: convId
				};
				chatMessages.update((msgs) => [...msgs, placeholder]);
			}
			break;
		}

		case 'chat_stream_chunk': {
			const chunk = raw.content as string;
			if (chunk) {
				const currentStreamId = get(streamingMessageId);
				chatMessages.update((msgs) => {
					const last = msgs[msgs.length - 1];
					if (last && last.role === 'assistant' && last.message_id === currentStreamId) {
						return [...msgs.slice(0, -1), { ...last, content: last.content + chunk }];
					}
					return msgs;
				});
			}
			break;
		}

		case 'chat_stream_tool': {
			const toolName = raw.tool as string;
			const status = raw.status as string;
			if (status === 'calling') {
				activeTools.update((t) => [...t, toolName]);
			} else if (status === 'done') {
				activeTools.update((t) => t.filter((n) => n !== toolName));
			}
			break;
		}

		case 'chat_stream_done': {
			const chatMsg = raw.message as ChatMessage | undefined;
			if (chatMsg) {
				// Match by the message's own id (the placeholder carries the same
				// id): after a mid-stream reload the DB row is already in the list,
				// so matching by streamingMessageId alone could duplicate it.
				const convId = chatMsg.conversation_id;
				if (!convId || get(activeConversationId) === convId) {
					chatMessages.update((msgs) => {
						const idx = msgs.findIndex((m) => m.message_id === chatMsg.message_id);
						if (idx >= 0) {
							const updated = [...msgs];
							updated[idx] = chatMsg;
							return updated;
						}
						return [...msgs, chatMsg];
					});
				}
			}
			streamingMessageId.set(null);
			activeTools.set([]);
			chatSending.set(false);
			// Refresh conversations list to update preview/timestamp
			engineApi.getConversations(100).then((list) => conversations.set(list)).catch(() => {});
			break;
		}

		case 'conversation_title_updated': {
			// Backend finished router-model title generation — patch the store so
			// the sidebar header and list row update in-place.
			const convId = raw.conversation_id as string | undefined;
			const newTitle = raw.title as string | undefined;
			if (convId && newTitle) {
				conversations.update((list) =>
					list.map((c) => (c.conversation_id === convId ? { ...c, title: newTitle } : c))
				);
			}
			break;
		}

		// Legacy non-streaming fallback
		case 'chat_response': {
			const payload = raw.payload as { message?: ChatMessage } | undefined;
			if (payload?.message) {
				chatMessages.update((msgs) => [...msgs, payload.message as ChatMessage]);
				chatSending.set(false);
			}
			break;
		}
	}
}

function handleWsStatus(status: WsStatus): void {
	if (status === 'disconnected' && (get(streamingMessageId) || get(chatSending))) {
		// The engine keeps processing (and persisting) the reply, but its stream
		// events can no longer reach us — and the done-event may be lost entirely.
		// Clear the busy state so the input can't stay disabled forever (that was
		// how a lost stream used to silently brick sending), and remember the
		// conversation so its persisted content can be re-pulled on reconnect.
		interruptedConvId = get(activeConversationId);
		streamingMessageId.set(null);
		activeTools.set([]);
		chatSending.set(false);
	}
	if (status === 'connected' && prevWsStatus !== 'connected' && interruptedConvId) {
		const convId = interruptedConvId;
		interruptedConvId = null;
		// Self-heal: the engine flushes partial content to the DB during the
		// stream, so a reload after reconnect shows whatever survived the gap
		// (and the final reply, if it finished while we were away).
		if (get(activeConversationId) === convId) {
			engineApi
				.getConversationMessages(convId, 50)
				.then((msgs) => applyLoadedMessages(convId, msgs.reverse()))
				.catch(() => {});
		}
	}
	prevWsStatus = status;
}
