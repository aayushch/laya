<script lang="ts">
	import { onMount } from 'svelte';
	import {
		chatOpen,
		chatMessages,
		chatInputPreset,
		streamingMessageId,
		activeTools
	} from '$lib/stores/chat';
	import { wsStatus, lastMessage, sendMessage } from '$lib/stores/websocket';
	import { engineApi } from '$lib/api/engine';
	import type { ChatMessage as ChatMessageType } from '$lib/api/types';
	import ChatMessage from './ChatMessage.svelte';

	let input = $state('');
	let sending = $state(false);
	let messagesEl: HTMLDivElement | undefined = $state();
	let textareaEl: HTMLTextAreaElement | undefined = $state();
	let loaded = $state(false);

	async function loadHistory() {
		if (loaded) return;
		try {
			const history = await engineApi.getChatHistory(50);
			chatMessages.set(history.reverse());
		} catch {
			// silently fail — chat history is optional
		}
		loaded = true;
	}

	function scrollToBottom() {
		if (messagesEl) {
			messagesEl.scrollTop = messagesEl.scrollHeight;
		}
	}

	// Load history when sidebar opens
	$effect(() => {
		if ($chatOpen) {
			loadHistory();
		}
	});

	// Apply preset message when triggered from a card
	$effect(() => {
		const preset = $chatInputPreset;
		if (preset) {
			input = preset;
			chatInputPreset.set('');
		}
	});

	// Auto-resize textarea to fit content, capped at 240px
	$effect(() => {
		if (!textareaEl) return;
		input; // track changes
		textareaEl.style.height = 'auto';
		textareaEl.style.height = Math.min(textareaEl.scrollHeight, 240) + 'px';
	});

	// Scroll to bottom when messages change
	$effect(() => {
		if ($chatMessages.length > 0) {
			setTimeout(scrollToBottom, 0);
		}
	});

	// Handle streaming WS events
	$effect(() => {
		const msg = $lastMessage;
		if (!msg) return;

		// Cast to any-like for streaming fields that live at the top level
		const raw = msg as unknown as Record<string, unknown>;

		switch (msg.type) {
			case 'chat_stream_start': {
				const msgId = raw.message_id as string;
				streamingMessageId.set(msgId);
				activeTools.set([]);
				// Add placeholder message
				const placeholder: ChatMessageType = {
					message_id: msgId,
					timestamp: new Date().toISOString(),
					role: 'assistant',
					content: '',
					referenced_cards: [],
					referenced_events: []
				};
				chatMessages.update((msgs) => [...msgs, placeholder]);
				break;
			}

			case 'chat_stream_chunk': {
				const chunk = raw.content as string;
				if (chunk) {
					chatMessages.update((msgs) => {
						const last = msgs[msgs.length - 1];
						if (last && last.role === 'assistant' && last.message_id === $streamingMessageId) {
							return [
								...msgs.slice(0, -1),
								{ ...last, content: last.content + chunk }
							];
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
				const chatMsg = raw.message as ChatMessageType;
				if (chatMsg) {
					// Replace the streaming placeholder with the final message
					chatMessages.update((msgs) => {
						const idx = msgs.findIndex((m) => m.message_id === $streamingMessageId);
						if (idx >= 0) {
							const updated = [...msgs];
							updated[idx] = chatMsg;
							return updated;
						}
						return [...msgs, chatMsg];
					});
				}
				streamingMessageId.set(null);
				activeTools.set([]);
				sending = false;
				break;
			}

			// Legacy non-streaming fallback
			case 'chat_response': {
				const payload = msg.payload as unknown as { message: ChatMessageType };
				if (payload?.message) {
					chatMessages.update((msgs) => [...msgs, payload.message]);
					sending = false;
				}
				break;
			}
		}
	});

	async function send() {
		const text = input.trim();
		if (!text || sending) return;

		const userMsg: ChatMessageType = {
			message_id: `tmp-${Date.now()}`,
			timestamp: new Date().toISOString(),
			role: 'user',
			content: text,
			referenced_cards: [],
			referenced_events: []
		};
		chatMessages.update((msgs) => [...msgs, userMsg]);
		input = '';
		sending = true;

		// Try WS first, fallback to REST
		let wsConnected = false;
		const unsub = wsStatus.subscribe((s) => (wsConnected = s === 'connected'));
		unsub();

		if (wsConnected) {
			sendMessage({ type: 'chat_message', payload: { message: text } });
		} else {
			try {
				const resp = await engineApi.sendChat(text);
				chatMessages.update((msgs) => [...msgs, resp.message]);
			} catch {
				const errMsg: ChatMessageType = {
					message_id: `err-${Date.now()}`,
					timestamp: new Date().toISOString(),
					role: 'assistant',
					content: 'Failed to send message. Please try again.',
					referenced_cards: [],
					referenced_events: []
				};
				chatMessages.update((msgs) => [...msgs, errMsg]);
			} finally {
				sending = false;
			}
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	}
</script>

{#if $chatOpen}
	<aside
		class="fixed bottom-0 right-0 z-50 flex w-[460px] flex-col border-l border-surface-700 bg-surface-900"
		style="top: var(--header-h);"
	>
		<!-- Header -->
		<div class="flex items-center justify-between border-b border-surface-700 px-4 py-3">
			<h3 class="text-sm font-semibold">Chat with Laya</h3>
			<button
				onclick={() => chatOpen.set(false)}
			aria-label="Close chat"
				class="text-surface-400 transition-colors hover:text-surface-200"
			>
				<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>

		<!-- Messages -->
		<div bind:this={messagesEl} class="flex-1 space-y-3 overflow-auto p-4">
			{#if $chatMessages.length === 0}
				<p class="text-center text-sm text-surface-500">
					Ask Laya about your events, cards, or recent activity.
				</p>
			{:else}
				{#each $chatMessages as msg (msg.message_id)}
					<ChatMessage message={msg} streaming={msg.message_id === $streamingMessageId} />
				{/each}
			{/if}

			<!-- Tool calling indicator -->
			{#if $activeTools.length > 0}
				<div class="flex justify-start">
					<div class="rounded-xl bg-surface-800 px-3.5 py-2 text-xs text-surface-400 ring-1 ring-surface-600">
						<span class="mr-1.5 inline-block h-2 w-2 animate-pulse rounded-full bg-laya-orange"></span>
						Looking up: {$activeTools.join(', ')}
					</div>
				</div>
			{/if}

			<!-- Waiting indicator (before stream starts) -->
			{#if sending && !$streamingMessageId && $activeTools.length === 0}
				<div class="flex justify-start">
					<div class="rounded-xl bg-surface-700 px-3.5 py-2.5 text-sm text-surface-400">
						<span class="inline-flex gap-1">
							<span class="animate-bounce">.</span>
							<span class="animate-bounce" style="animation-delay: 0.1s">.</span>
							<span class="animate-bounce" style="animation-delay: 0.2s">.</span>
						</span>
					</div>
				</div>
			{/if}
		</div>

		<!-- Input -->
		<div class="border-t border-surface-700 p-4">
			<div class="relative">
				<textarea
					bind:this={textareaEl}
					bind:value={input}
					onkeydown={handleKeydown}
					placeholder="Ask something..."
					rows={3}
					style="min-height: 4.5rem; overflow-y: auto;"
					class="w-full resize-none rounded-lg border border-surface-600 bg-surface-800 py-2 pl-3 pr-10 text-sm text-surface-200 placeholder-surface-500 focus:border-laya-orange/50 focus:outline-none"
				></textarea>
				<button
					onclick={send}
					disabled={!input.trim() || sending}
					aria-label="Send message"
					class="absolute bottom-2 right-2 rounded-md p-1 transition-colors disabled:opacity-30
						{input.trim() ? 'text-laya-orange hover:text-laya-peach' : 'text-surface-600'}"
				>
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M12 5l7 7-7 7" />
					</svg>
				</button>
			</div>
		</div>
	</aside>
{/if}
