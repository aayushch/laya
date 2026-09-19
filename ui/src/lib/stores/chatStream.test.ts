// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import type { ChatMessage } from '$lib/api/types';
import { mergeStreamingIntoLoaded, applyLoadedMessages } from './chatStream';
import { chatMessages, streamingMessageId } from './chat';

function msg(id: string, role: 'user' | 'assistant', content: string, convId = 'conv_1'): ChatMessage {
	return {
		message_id: id,
		timestamp: '2026-08-24 10:00:00',
		role,
		content,
		referenced_cards: [],
		referenced_events: [],
		conversation_id: convId
	};
}

beforeEach(() => {
	chatMessages.set([]);
	streamingMessageId.set(null);
});

describe('mergeStreamingIntoLoaded', () => {
	it('returns the loaded list untouched when nothing is streaming', () => {
		const loaded = [msg('m1', 'user', 'hi'), msg('m2', 'assistant', 'hello')];
		expect(mergeStreamingIntoLoaded(loaded, undefined, 'conv_1')).toBe(loaded);
	});

	it('replaces the stale DB copy of the streaming message with the live one', () => {
		// The engine flushes partial content on a ~1s throttle, so the DB row
		// lags the live store version — the live one must win.
		const loaded = [msg('m1', 'user', 'hi'), msg('m2', 'assistant', 'partial')];
		const live = msg('m2', 'assistant', 'partial plus newer chunks');
		const merged = mergeStreamingIntoLoaded(loaded, live, 'conv_1');
		expect(merged).toHaveLength(2);
		expect(merged[1].content).toBe('partial plus newer chunks');
	});

	it('appends the live streaming message when the DB load does not have it yet', () => {
		const loaded = [msg('m1', 'user', 'hi')];
		const live = msg('m2', 'assistant', 'streaming…');
		const merged = mergeStreamingIntoLoaded(loaded, live, 'conv_1');
		expect(merged.map((m) => m.message_id)).toEqual(['m1', 'm2']);
	});

	it('does not leak a stream belonging to a different conversation', () => {
		const loaded = [msg('a1', 'user', 'other thread', 'conv_2')];
		const live = msg('m2', 'assistant', 'streaming…', 'conv_1');
		expect(mergeStreamingIntoLoaded(loaded, live, 'conv_2')).toBe(loaded);
	});
});

describe('applyLoadedMessages', () => {
	it('preserves the live streaming placeholder across a DB reload', () => {
		streamingMessageId.set('m2');
		chatMessages.set([msg('m1', 'user', 'hi'), msg('m2', 'assistant', 'live content ahead of DB')]);
		applyLoadedMessages('conv_1', [msg('m1', 'user', 'hi'), msg('m2', 'assistant', 'stale flush')]);
		const result = get(chatMessages);
		expect(result[1].content).toBe('live content ahead of DB');
	});

	it('plain-sets when no stream is active', () => {
		chatMessages.set([msg('mX', 'assistant', 'old view')]);
		applyLoadedMessages('conv_1', [msg('m1', 'user', 'hi')]);
		expect(get(chatMessages).map((m) => m.message_id)).toEqual(['m1']);
	});
});
