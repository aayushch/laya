import { writable } from 'svelte/store';
import type { ChatMessage } from '$lib/api/types';

export const chatOpen = writable(false);
export const chatMessages = writable<ChatMessage[]>([]);
export const chatInputPreset = writable('');

/** ID of the message currently being streamed (null when idle) */
export const streamingMessageId = writable<string | null>(null);

/** Tools currently being called by the assistant */
export const activeTools = writable<string[]>([]);
