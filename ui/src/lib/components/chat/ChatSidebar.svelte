<script lang="ts">
	import { onMount } from 'svelte';
	import { chatOpen, chatMessages } from '$lib/stores/chat';
	import { wsStatus, lastMessage, sendMessage } from '$lib/stores/websocket';
	import { engineApi } from '$lib/api/engine';
	import type { ChatMessage as ChatMessageType } from '$lib/api/types';
	import ChatMessage from './ChatMessage.svelte';

	let input = $state('');
	let sending = $state(false);
	let messagesEl: HTMLDivElement | undefined = $state();
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

	// Scroll to bottom when messages change
	$effect(() => {
		if ($chatMessages.length > 0) {
			// Use tick to ensure DOM update
			setTimeout(scrollToBottom, 0);
		}
	});

	// Listen for WS chat responses
	$effect(() => {
		const msg = $lastMessage;
		if (msg?.type === 'chat_response' && msg.payload) {
			const chatMsg = msg.payload as unknown as { message: ChatMessageType };
			if (chatMsg.message) {
				chatMessages.update((msgs) => [...msgs, chatMsg.message]);
				sending = false;
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
		class="fixed right-0 top-0 z-40 flex h-screen w-[400px] flex-col border-l border-surface-700 bg-surface-900"
	>
		<!-- Header -->
		<div class="flex items-center justify-between border-b border-surface-700 px-4 py-3">
			<h3 class="text-sm font-semibold">Chat with Laya</h3>
			<button
				onclick={() => chatOpen.set(false)}
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
					<ChatMessage message={msg} />
				{/each}
			{/if}
			{#if sending}
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
			<div class="flex gap-2">
				<textarea
					bind:value={input}
					onkeydown={handleKeydown}
					placeholder="Ask something..."
					rows={1}
					class="flex-1 resize-none rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-surface-200 placeholder-surface-500 focus:border-blue-500 focus:outline-none"
				></textarea>
				<button
					onclick={send}
					disabled={!input.trim() || sending}
					class="rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-500 disabled:opacity-50"
				>
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M12 5l7 7-7 7" />
					</svg>
				</button>
			</div>
		</div>
	</aside>
{/if}
