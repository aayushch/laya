<script lang="ts">
	import { get } from 'svelte/store';
	import {
		chatOpen,
		chatMessages,
		chatInputPreset,
		streamingMessageId,
		activeTools,
		activeConversationId,
		conversations,
		chatListOpen
	} from '$lib/stores/chat';
	import { wsStatus, lastMessage, sendMessage } from '$lib/stores/websocket';
	import { engineApi } from '$lib/api/engine';
	import type { ChatMessage as ChatMessageType } from '$lib/api/types';
	import ChatMessage from './ChatMessage.svelte';
	import ChatConversationList from './ChatConversationList.svelte';
	import { fly } from 'svelte/transition';
	import { tick } from 'svelte';

	let input = $state('');
	let sending = $state(false);
	let messagesEl: HTMLDivElement | undefined = $state();
	let textareaEl: HTMLTextAreaElement | undefined = $state();
	let renaming = $state(false);
	let renameValue = $state('');
	let renameInputEl = $state<HTMLInputElement | undefined>();

	// Show list view when explicitly requested (chatListOpen controls this)
	const showList = $derived($chatListOpen);

	function scrollToBottom() {
		if (messagesEl) {
			messagesEl.scrollTop = messagesEl.scrollHeight;
		}
	}

	function resizeTextarea() {
		if (!textareaEl) return;
		textareaEl.style.height = 'auto';
		textareaEl.style.height = Math.min(textareaEl.scrollHeight, 240) + 'px';
	}

	// Apply preset message when triggered from a card
	$effect(() => {
		const preset = $chatInputPreset;
		if (preset) {
			input = preset;
			chatInputPreset.set('');
			// If showing list, switch to chat view (will auto-create conversation on send)
			if ($chatListOpen) {
				activeConversationId.set(null);
				chatMessages.set([]);
				chatListOpen.set(false);
			}
			// Wait for the textarea to render, then resize to fit the preset text
			tick().then(resizeTextarea);
		}
	});

	// Clear input when switching to the conversation list (new chat)
	$effect(() => {
		if ($chatListOpen) {
			input = '';
		}
	});

	// Auto-resize textarea to fit content, capped at 240px
	$effect(() => {
		if (!textareaEl) return;
		input; // track changes
		resizeTextarea();
	});

	// Scroll to bottom when messages change
	$effect(() => {
		if ($chatMessages.length > 0) {
			setTimeout(scrollToBottom, 0);
		}
	});

	// Handle streaming WS events
	// NOTE: We use get(streamingMessageId) instead of $streamingMessageId to avoid
	// tracking it as a reactive dependency, which would re-trigger this effect
	// when streamingMessageId changes and cause duplicate message processing.
	$effect(() => {
		const msg = $lastMessage;
		if (!msg) return;

		const raw = msg as unknown as Record<string, unknown>;

		switch (msg.type) {
			case 'chat_stream_start': {
				const msgId = raw.message_id as string;
				const convId = raw.conversation_id as string | undefined;
				streamingMessageId.set(msgId);
				activeTools.set([]);
				// Track the conversation if auto-created by backend
				if (convId && !get(activeConversationId)) {
					activeConversationId.set(convId);
				}
				// Add placeholder message
				const placeholder: ChatMessageType = {
					message_id: msgId,
					timestamp: new Date().toISOString(),
					role: 'assistant',
					content: '',
					referenced_cards: [],
					referenced_events: [],
					conversation_id: convId
				};
				chatMessages.update((msgs) => [...msgs, placeholder]);
				break;
			}

			case 'chat_stream_chunk': {
				const chunk = raw.content as string;
				if (chunk) {
					const currentStreamId = get(streamingMessageId);
					chatMessages.update((msgs) => {
						const last = msgs[msgs.length - 1];
						if (last && last.role === 'assistant' && last.message_id === currentStreamId) {
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
					const currentStreamId = get(streamingMessageId);
					chatMessages.update((msgs) => {
						const idx = msgs.findIndex((m) => m.message_id === currentStreamId);
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
				// Refresh conversations list to update preview/timestamp
				engineApi.getConversations(100).then((list) => conversations.set(list)).catch(() => {});
				break;
			}

			case 'conversation_title_updated': {
				// Backend finished router-model title generation — patch the
				// store so the sidebar header and list row update in-place.
				const convId = raw.conversation_id as string | undefined;
				const newTitle = raw.title as string | undefined;
				if (convId && newTitle) {
					conversations.update((list) =>
						list.map((c) =>
							c.conversation_id === convId ? { ...c, title: newTitle } : c
						)
					);
				}
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

		const convId = get(activeConversationId);

		const userMsg: ChatMessageType = {
			message_id: `tmp-${Date.now()}`,
			timestamp: new Date().toISOString(),
			role: 'user',
			content: text,
			referenced_cards: [],
			referenced_events: [],
			conversation_id: convId ?? undefined
		};
		chatMessages.update((msgs) => [...msgs, userMsg]);
		input = '';
		sending = true;

		// Try WS first, fallback to REST
		let wsConnected = false;
		const unsub = wsStatus.subscribe((s) => (wsConnected = s === 'connected'));
		unsub();

		if (wsConnected) {
			sendMessage({
				type: 'chat_message',
				payload: { message: text, conversation_id: convId }
			});
		} else {
			try {
				const resp = await engineApi.sendChat(text, convId ?? undefined);
				chatMessages.update((msgs) => [...msgs, resp.message]);
				// Track auto-created conversation
				if (resp.message.conversation_id && !convId) {
					activeConversationId.set(resp.message.conversation_id);
				}
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

	function goBackToList() {
		chatListOpen.set(true);
	}

	function conversationTitle(): string {
		const convId = $activeConversationId;
		if (!convId) return 'New Chat';
		const conv = $conversations.find((c) => c.conversation_id === convId);
		return conv?.title ?? 'Chat';
	}

	function startRename() {
		const convId = get(activeConversationId);
		if (!convId) return;
		renameValue = conversationTitle();
		renaming = true;
	}

	// Focus + select once the input is mounted
	$effect(() => {
		if (renaming && renameInputEl) {
			renameInputEl.focus();
			renameInputEl.select();
		}
	});

	async function commitRename() {
		if (!renaming) return;
		const convId = get(activeConversationId);
		renaming = false;
		if (!convId) return;

		const trimmed = renameValue.trim().slice(0, 100);
		if (!trimmed) return;

		let prior = '';
		conversations.update((list) => {
			const found = list.find((c) => c.conversation_id === convId);
			prior = found?.title ?? '';
			return list.map((c) =>
				c.conversation_id === convId ? { ...c, title: trimmed } : c
			);
		});
		if (prior === trimmed) return;

		try {
			await engineApi.renameConversation(convId, trimmed);
		} catch {
			conversations.update((list) =>
				list.map((c) => (c.conversation_id === convId ? { ...c, title: prior } : c))
			);
		}
	}

	function cancelRename() {
		renaming = false;
	}

	function handleRenameKey(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			commitRename();
		} else if (e.key === 'Escape') {
			e.preventDefault();
			cancelRename();
		}
	}
</script>

{#if $chatOpen}
	<aside
		class="fixed bottom-0 right-0 z-40 flex w-[460px] flex-col"
		style="top: var(--header-h, 45px);"
		transition:fly={{ x: 460, duration: 250, opacity: 1 }}
	>
	<!-- Inner container: top margin aligns with main content padding (p-4 = 1rem) so the panel
	     starts at the same height as the feed toolbar / page header controls -->
	<div class="flex flex-1 flex-col overflow-hidden mt-4 mr-4 rounded-xl border border-surface-700 bg-surface-900">
		{#if showList}
			<ChatConversationList />
		{:else}
			<!-- Active Chat Header -->
			<div class="flex items-center justify-between border-b border-surface-700 px-4 py-3">
				<div class="group flex items-center gap-2 min-w-0">
					<button
						onclick={goBackToList}
						aria-label="Back to conversations"
						class="shrink-0 rounded-md p-1 text-surface-400 transition-colors hover:text-surface-200"
					>
						<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
						</svg>
					</button>
					{#if renaming}
						<input
							bind:this={renameInputEl}
							bind:value={renameValue}
							onkeydown={handleRenameKey}
							onblur={commitRename}
							maxlength={100}
							aria-label="Rename conversation"
							class="min-w-0 flex-1 rounded border border-laya-orange/40 bg-surface-800 px-1.5 py-0.5 text-sm font-semibold text-surface-100 focus:border-laya-orange focus:outline-none"
						/>
					{:else}
						<button
							type="button"
							onclick={startRename}
							disabled={!$activeConversationId}
							class="flex min-w-0 items-center gap-1.5 rounded px-1 py-0.5 text-left transition-colors enabled:hover:bg-surface-800 disabled:cursor-default"
							title={$activeConversationId ? 'Rename conversation' : ''}
						>
							<h3 class="truncate text-sm font-semibold">{conversationTitle()}</h3>
							{#if $activeConversationId}
								<svg
									class="h-3 w-3 shrink-0 text-surface-500 opacity-0 transition-opacity group-hover:opacity-100"
									fill="none"
									stroke="currentColor"
									viewBox="0 0 24 24"
								>
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
								</svg>
							{/if}
						</button>
					{/if}
				</div>
				<button
					onclick={() => chatOpen.set(false)}
					aria-label="Close chat"
					class="shrink-0 text-surface-400 transition-colors hover:text-surface-200"
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
						<div class="rounded-xl bg-surface-700 px-3.5 py-2.5 text-laya-base text-surface-400">
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
						class="w-full resize-none rounded-lg border border-surface-600 bg-surface-800 py-2 pl-3 pr-10 text-laya-base text-surface-200 placeholder-surface-500 focus:border-laya-orange/50 focus:outline-none"
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
		{/if}
	</div>
	</aside>
{/if}
