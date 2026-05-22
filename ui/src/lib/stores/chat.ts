// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

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

/** Card ID to navigate to in the feed (set by chat card links, consumed by feed page) */
export const pendingCardId = writable<string | null>(null);

/** Card context string for system prompt injection (hidden from user input) */
export const chatCardContext = writable<string | null>(null);

/** Card IDs for conversation anchoring (used with card-context chats) */
export const chatCardIds = writable<string[] | null>(null);
