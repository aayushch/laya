import { writable } from 'svelte/store';
import type { ChatMessage, Conversation } from '$lib/api/types';

export const chatOpen = writable(false);
export const chatMessages = writable<ChatMessage[]>([]);
export const chatInputPreset = writable('');

/** ID of the message currently being streamed (null when idle) */
export const streamingMessageId = writable<string | null>(null);

/** Tools currently being called by the assistant */
export const activeTools = writable<string[]>([]);

/** Active conversation ID (null = no conversation selected, show list) */
export const activeConversationId = writable<string | null>(null);

/** All conversations for the list view */
export const conversations = writable<Conversation[]>([]);

/** Whether the chat list panel is shown (vs the active chat view) */
export const chatListOpen = writable(true);
