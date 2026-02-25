import { writable } from 'svelte/store';
import type { ChatMessage } from '$lib/api/types';

export const chatOpen = writable(false);
export const chatMessages = writable<ChatMessage[]>([]);
