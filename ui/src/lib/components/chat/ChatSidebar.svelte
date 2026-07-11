<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
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
		chatListOpen,
		chatExpanded,
		chatCardContext,
		chatCardIds
	} from '$lib/stores/chat';
	import { wsStatus, lastMessage, sendMessage } from '$lib/stores/websocket';
	import { engineApi } from '$lib/api/engine';
	import type { ChatMessage as ChatMessageType } from '$lib/api/types';
	import ChatMessage from './ChatMessage.svelte';
	import ChatConversationList from './ChatConversationList.svelte';
	import { fly, fade } from 'svelte/transition';
	import { tick } from 'svelte';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { glassTheme } from '$lib/stores/glassTheme';

	let input = $state('');
	let sending = $state(false);
	let clearPending = $state(false);
	let messagesEl: HTMLDivElement | undefined = $state();
	let textareaEl: HTMLTextAreaElement | undefined = $state();
	let renaming = $state(false);
	let renameValue = $state('');
	let renameInputEl = $state<HTMLInputElement | undefined>();

	// Auto-scroll follows new content only while the user is pinned to the
	// bottom. If they scroll up to read earlier content (e.g. during streaming),
	// we stop yanking the viewport. Re-pins when they scroll back to the bottom.
	let pinnedToBottom = $state(true);
	const SCROLL_PIN_THRESHOLD = 40;

	// Show list view when explicitly requested (chatListOpen controls this)
	const showList = $derived($chatListOpen);

	// Reset the wide overlay whenever the chat closes, so reopening always starts
	// in the default sidebar layout (the expand state is intentionally ephemeral).
	$effect(() => {
		if (!$chatOpen) chatExpanded.set(false);
	});

	function handleSidebarKeydown(e: KeyboardEvent) {
		// Escape collapses the wide overlay back to the normal sidebar (without
		// closing the chat). When not expanded, let Escape bubble normally.
		if (e.key === 'Escape' && $chatExpanded) {
			e.stopPropagation();
			chatExpanded.set(false);
		}
	}

	function scrollToBottom() {
		if (messagesEl) {
			messagesEl.scrollTop = messagesEl.scrollHeight;
		}
	}

	function handleMessagesScroll() {
		if (!messagesEl) return;
		const distanceFromBottom =
			messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight;
		pinnedToBottom = distanceFromBottom <= SCROLL_PIN_THRESHOLD;
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

	const inCardMode = $derived(!!$chatCardIds && $chatCardIds.length > 0);

	// When card context is set and drawer opens, restore or start a card-anchored conversation
	$effect(() => {
		const cardIds = $chatCardIds;
		if (!cardIds || cardIds.length === 0 || !$chatOpen) return;
		chatListOpen.set(false);

		(async () => {
			try {
				const conv = await engineApi.getConversationByCards(cardIds);
				if (conv) {
					activeConversationId.set(conv.conversation_id);
					const msgs = await engineApi.getConversationMessages(conv.conversation_id, 100);
					chatMessages.set([...msgs].reverse());
				} else {
					activeConversationId.set(null);
					chatMessages.set([]);
				}
			} catch {
				activeConversationId.set(null);
				chatMessages.set([]);
			}
		})();
	});

	// Clear input when switching to the conversation list (new chat)
	$effect(() => {
		if ($chatListOpen) {
			input = '';
		}
	});

	// Focus the input when entering the chat view (e.g. after starting a new
	// chat via '+', or opening a conversation) so the user can type immediately
	// without having to click into the textarea first. The textarea only renders
	// once we leave the list view, so wait a tick for it to mount.
	$effect(() => {
		if ($chatOpen && !showList) {
			tick().then(() => textareaEl?.focus());
		}
	});

	// Auto-resize textarea to fit content, capped at 240px
	$effect(() => {
		if (!textareaEl) return;
		input; // track changes
		resizeTextarea();
	});

	// Scroll to bottom when messages change, but only if the user hasn't
	// scrolled up to read earlier content. This lets streamed content flow
	// naturally without hijacking the scroll when the user is reading.
	$effect(() => {
		if ($chatMessages.length > 0 && pinnedToBottom) {
			setTimeout(scrollToBottom, 0);
		}
	});

	// Re-pin to bottom when switching conversations so the new thread opens
	// at its latest message instead of inheriting the previous scroll state.
	$effect(() => {
		$activeConversationId;
		pinnedToBottom = true;
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
		pinnedToBottom = true;

		// Try WS first, fallback to REST
		let wsConnected = false;
		const unsub = wsStatus.subscribe((s) => (wsConnected = s === 'connected'));
		unsub();

		if (wsConnected) {
			sendMessage({
				type: 'chat_message',
				payload: {
					message: text,
					conversation_id: convId,
					...(get(chatCardContext) ? { card_context: get(chatCardContext) } : {}),
					...(get(chatCardIds)?.length ? { card_ids: get(chatCardIds) } : {})
				}
			});
		} else {
			try {
				const resp = await engineApi.sendChat(
					text,
					convId ?? undefined,
					get(chatCardContext) ?? undefined,
					get(chatCardIds) ?? undefined
				);
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
		chatCardContext.set(null);
		chatCardIds.set(null);
		chatListOpen.set(true);
	}

	function handleClose() {
		chatOpen.set(false);
		chatCardContext.set(null);
		chatCardIds.set(null);
	}

	function clearChat() {
		if (clearPending) {
			// Second click — confirm
			const convId = get(activeConversationId);
			if (convId) {
				engineApi.deleteConversation(convId).catch(() => {});
				conversations.update((list) => list.filter((c) => c.conversation_id !== convId));
			}
			activeConversationId.set(null);
			chatMessages.set([]);
			clearPending = false;
			if (!get(chatCardIds)?.length) {
				chatListOpen.set(true);
			}
		} else {
			clearPending = true;
			setTimeout(() => { clearPending = false; }, 3000);
		}
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

{#if $chatOpen && $chatExpanded}
	<!-- Dim scrim behind the wide overlay for a focused feel; click to collapse.
	     Inset to the content band so the titlebar/footer stay visible & clickable.
	     z-30 keeps it below the panel (z-40). -->
	<button
		aria-label="Collapse chat"
		onclick={() => chatExpanded.set(false)}
		class="fixed inset-x-0 z-30 cursor-default chat-scrim backdrop-blur-sm"
		style="top: var(--header-h, 38px); bottom: var(--footer-h, 33px);"
		transition:fade={{ duration: $reducedMotion ? 0 : 150 }}
	></button>
{/if}
{#if $chatOpen}
	<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
	<aside
		onkeydown={handleSidebarKeydown}
		class="fixed right-0 z-40 flex flex-col transition-[width] duration-200 {$chatExpanded ? 'w-[75vw] min-w-[460px] max-w-[1100px]' : 'w-[460px]'}"
		style="top: var(--header-h, 45px); bottom: var(--footer-h, 33px);"
		transition:fly={{ x: 460, duration: $reducedMotion ? 0 : 250, opacity: 1 }}
	>
	<!-- Inner container: margins align with main content padding so the panel
	     floats above the footer with consistent spacing -->
	<div class="flex flex-1 flex-col overflow-hidden my-4 mr-4 rounded-xl {$glassTheme ? 'glass-section' : 'border border-surface-700 bg-surface-900'} {$chatExpanded ? 'shadow-2xl' : ''} {$glassTheme && $chatExpanded ? 'chat-overlay-surface' : ''}">
		{#if showList}
			<ChatConversationList />
		{:else}
			<!-- Active Chat Header -->
			<div class="flex items-center justify-between border-b {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700'} px-4 py-3">
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
					{#if inCardMode}
						<h3 class="truncate text-laya-base font-semibold">
							{$chatCardIds?.length === 1 ? 'Chat about this card' : `Chat about ${$chatCardIds?.length} cards`}
						</h3>
					{:else if renaming}
						<input
							bind:this={renameInputEl}
							bind:value={renameValue}
							onkeydown={handleRenameKey}
							onblur={commitRename}
							maxlength={100}
							aria-label="Rename conversation"
							class="min-w-0 flex-1 rounded border border-laya-orange/40 {$glassTheme ? 'bg-white/[0.05]' : 'bg-surface-800'} px-1.5 py-0.5 text-laya-base font-semibold text-surface-100 focus:border-laya-orange focus:outline-none"
						/>
					{:else}
						<button
							type="button"
							onclick={startRename}
							disabled={!$activeConversationId}
							class="flex min-w-0 items-center gap-1.5 rounded px-1 py-0.5 text-left transition-colors {$glassTheme ? 'enabled:hover:bg-white/[0.05]' : 'enabled:hover:bg-surface-800'} disabled:cursor-default"
							title={$activeConversationId ? 'Rename conversation' : ''}
						>
							<h3 class="truncate text-laya-base font-semibold">{conversationTitle()}</h3>
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
				<div class="flex items-center gap-1">
					{#if $activeConversationId || inCardMode}
						<div class="group/clr relative">
							<button
								onclick={clearChat}
								aria-label={clearPending ? 'Click again to confirm' : 'Clear chat'}
								class="shrink-0 rounded-md p-1 transition-colors
									{clearPending ? 'text-red-400' : 'text-surface-400 hover:text-red-400'}"
							>
								<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
								</svg>
							</button>
							<span class="pointer-events-none absolute right-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-transparent glass-tooltip glass-tooltip-dense px-2 py-1 text-[10px] font-medium shadow-lg
								opacity-0 transition-opacity duration-75 group-hover/clr:opacity-100
								{clearPending ? '!border-red-400/30 !bg-red-950 !text-red-300' : ''}">
								{clearPending ? 'Click again to confirm' : 'Clear chat'}
							</span>
						</div>
					{/if}
					<!-- Expand / collapse the wide overlay -->
					<button
						onclick={() => chatExpanded.set(!$chatExpanded)}
						aria-label={$chatExpanded ? 'Collapse chat' : 'Expand chat'}
						title={$chatExpanded ? 'Collapse' : 'Expand'}
						class="shrink-0 rounded-md p-1 text-surface-400 transition-colors hover:text-surface-200"
					>
						{#if $chatExpanded}
							<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M15 9h4.5M15 9V4.5M15 9l5.25-5.25M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 15h4.5M15 15v4.5M15 15l5.25 5.25" />
							</svg>
						{:else}
							<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15" />
							</svg>
						{/if}
					</button>
					<button
						onclick={handleClose}
						aria-label="Close chat"
						class="shrink-0 text-surface-400 transition-colors hover:text-surface-200"
					>
						<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
						</svg>
					</button>
				</div>
			</div>

			<!-- Messages -->
			<div bind:this={messagesEl} onscroll={handleMessagesScroll} class="flex-1 space-y-3 overflow-auto p-4">
				{#if $chatMessages.length === 0}
					<p class="text-center text-laya-base text-surface-500">
						{#if inCardMode}
							Ask anything about {$chatCardIds?.length === 1 ? 'this card' : `these ${$chatCardIds?.length} cards`}. Laya has full context of their intelligence, outputs, and metadata.
						{:else}
							Ask Laya about your events, cards, or recent activity.
						{/if}
					</p>
				{:else}
					{#each $chatMessages as msg (msg.message_id)}
						<ChatMessage message={msg} streaming={msg.message_id === $streamingMessageId} />
					{/each}
				{/if}

				<!-- Tool calling indicator -->
				{#if $activeTools.length > 0}
					<div class="flex justify-start">
						<div class="rounded-xl {$glassTheme ? 'bg-white/[0.05] ring-1 ring-white/[0.08]' : 'bg-surface-800 ring-1 ring-surface-600'} px-3.5 py-2 text-laya-secondary text-surface-400">
							<span class="mr-1.5 inline-block h-2 w-2 animate-pulse rounded-full bg-laya-orange"></span>
							Looking up: {$activeTools.join(', ')}
						</div>
					</div>
				{/if}

				<!-- Waiting indicator (before stream starts) -->
				{#if sending && !$streamingMessageId && $activeTools.length === 0}
					<div class="flex justify-start">
						<div class="rounded-xl {$glassTheme ? 'bg-white/[0.06]' : 'bg-surface-700'} px-3.5 py-2.5 text-laya-base text-surface-400">
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
			<div class="border-t {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700'} p-4">
				<div class="relative">
					<textarea
						bind:this={textareaEl}
						bind:value={input}
						onkeydown={handleKeydown}
						placeholder="Ask something..."
						rows={3}
						style="min-height: 4.5rem; overflow-y: auto;"
						class="w-full resize-none rounded-lg py-2 pl-3 pr-10 text-laya-base text-surface-200 placeholder-surface-500 focus:outline-none {$glassTheme ? 'glass-input' : 'border border-surface-500 bg-surface-800 focus:border-laya-orange/50'}"
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
