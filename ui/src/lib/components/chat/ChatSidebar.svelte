<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { get } from 'svelte/store';
	import { page } from '$app/stores';
	import {
		chatOpen,
		chatMessages,
		chatInputPreset,
		streamingMessageId,
		chatSending,
		activeTools,
		activeConversationId,
		conversations,
		chatListOpen,
		chatExpanded,
		chatCardContext,
		chatCardIds,
		chatDocumentIds
	} from '$lib/stores/chat';
	import { documents, readyDocuments, loadDocuments } from '$lib/stores/documents';
	import { attachDocumentIds, attachDroppedToChat, detachDocumentId } from '$lib/documents/chatAttach';
	import { statusLabel } from '$lib/documents/documentUpdateReducer';
	import { pickFilePaths } from '$lib/utils/fileDrop';
	import { goto } from '$app/navigation';
	import { applyLoadedMessages } from '$lib/stores/chatStream';
	import { wsStatus, sendMessage } from '$lib/stores/websocket';
	import { engineApi } from '$lib/api/engine';
	import type { ChatMessage as ChatMessageType } from '$lib/api/types';
	import ChatMessage from './ChatMessage.svelte';
	import ChatConversationList from './ChatConversationList.svelte';
	import { fly, fade } from 'svelte/transition';
	import { tick } from 'svelte';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { glassTheme } from '$lib/stores/glassTheme';

	let input = $state('');
	let clearPending = $state(false);
	let messagesEl: HTMLDivElement | undefined = $state();
	let textareaEl: HTMLTextAreaElement | undefined = $state();
	let renaming = $state(false);
	let renameValue = $state('');
	let renameInputEl = $state<HTMLInputElement | undefined>();

	// Corpus attachments: the paperclip popover lists ready documents to attach;
	// chips above the textarea show what is attached to this conversation.
	let attachOpen = $state(false);
	let attachFilter = $state('');
	let attachBusy = $state(false);
	let attachFileInput: HTMLInputElement | null = $state(null);
	const attachedIds = $derived($chatDocumentIds ?? []);
	const attachedDocs = $derived(
		attachedIds.map((id) => $documents.find((d) => d.document_id === id) ?? null)
	);
	const attachCandidates = $derived(
		$readyDocuments.filter((d) => {
			const q = attachFilter.trim().toLowerCase();
			return !q || d.title.toLowerCase().includes(q) || d.original_filename.toLowerCase().includes(q);
		})
	);

	function toggleAttach(id: string) {
		if (attachedIds.includes(id)) detachDocumentId(id);
		else attachDocumentIds([id]);
	}

	async function addFileToChat() {
		attachBusy = true;
		try {
			const paths = await pickFilePaths([]);
			if (paths) {
				await attachDroppedToChat({ paths });
				return;
			}
			attachFileInput?.click();
		} finally {
			attachBusy = false;
		}
	}

	async function handleAttachFilePicked(e: Event) {
		const target = e.target as HTMLInputElement;
		const files = target.files ? Array.from(target.files) : [];
		target.value = '';
		if (files.length === 0) return;
		attachBusy = true;
		try {
			await attachDroppedToChat({ files });
		} finally {
			attachBusy = false;
		}
	}

	$effect(() => {
		if (attachOpen && $documents.length === 0) loadDocuments();
	});

	// Auto-scroll follows new content only while the user is pinned to the
	// bottom. If they scroll up to read earlier content (e.g. during streaming),
	// we stop yanking the viewport. Re-pins when they scroll back to the bottom.
	let pinnedToBottom = $state(true);
	const SCROLL_PIN_THRESHOLD = 40;

	// Show list view when explicitly requested (chatListOpen controls this)
	const showList = $derived($chatListOpen);

	// Busy = a send is in flight or a reply is streaming. Store-backed (see
	// stores/chat.ts) so it survives close/reopen and gets cleared by the
	// module-level chatStream handler — the old component-local `sending` flag
	// stayed true forever when a stream never delivered its done-event, which
	// silently blocked all further sends.
	const chatBusy = $derived($chatSending || $streamingMessageId !== null);

	// The floating-card skin (margins + rounding + shadow) suits the padded pages, but on
	// the workspace the surrounding panels are full-bleed — so that skin reads as a
	// mismatched slab bolted on beside them (misaligned top/bottom, corners notching the
	// flat neighbour). When docked on the workspace, and NOT in the wide expanded overlay,
	// render flush instead: a full-height, edge-to-edge glass-panel with a left/top divider
	// that continues the workspace's panel row. Bound to the LIVE route (not the route it
	// opened on) so navigating to a padded page restores the floating card automatically.
	const dockedFlush = $derived($page.url.pathname.startsWith('/workspace') && !$chatExpanded);
	const surfaceClass = $derived(
		dockedFlush
			? `border-l border-t ${$glassTheme ? 'glass-panel border-white/[0.06]' : 'border-surface-700 bg-surface-900'}`
			: `my-4 mr-4 rounded-xl ${$glassTheme ? 'glass-section' : 'border border-surface-700 bg-surface-900'}${$chatExpanded ? ' shadow-2xl' : ''}${$glassTheme && $chatExpanded ? ' chat-overlay-surface' : ''}`
	);

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
	const inDocMode = $derived(attachedIds.length > 0);

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
					// Merge-aware: don't clobber an in-flight streaming reply
					applyLoadedMessages(conv.conversation_id, [...msgs].reverse());
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

	// Streaming WS events are handled by the module-level subscriber in
	// $lib/stores/chatStream.ts (initialized from the root layout), NOT here —
	// an in-component handler dies with the component on close, dropping every
	// chunk of a still-running chat and making it look aborted on reopen.

	async function send() {
		const text = input.trim();
		if (!text || chatBusy) return;

		const convId = get(activeConversationId);

		const userMsg: ChatMessageType = {
			message_id: `tmp-${Date.now()}`,
			timestamp: new Date().toISOString(),
			role: 'user',
			content: text,
			referenced_cards: [],
			referenced_events: [],
			referenced_documents: [],
			conversation_id: convId ?? undefined
		};
		chatMessages.update((msgs) => [...msgs, userMsg]);
		input = '';
		chatSending.set(true);
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
					...(get(chatCardIds)?.length ? { card_ids: get(chatCardIds) } : {}),
					// null = keep the conversation's stored attachments; a list (even
					// empty, after the last chip was removed) replaces them.
					...(get(chatDocumentIds) !== null ? { document_ids: get(chatDocumentIds) } : {})
				}
			});
		} else {
			try {
				const resp = await engineApi.sendChat(
					text,
					convId ?? undefined,
					get(chatCardContext) ?? undefined,
					get(chatCardIds) ?? undefined,
					get(chatDocumentIds) ?? undefined
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
					referenced_events: [],
					referenced_documents: []
				};
				chatMessages.update((msgs) => [...msgs, errMsg]);
			} finally {
				chatSending.set(false);
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
		chatDocumentIds.set(null);
		attachOpen = false;
		chatListOpen.set(true);
	}

	function handleClose() {
		chatOpen.set(false);
		chatCardContext.set(null);
		chatCardIds.set(null);
		chatDocumentIds.set(null);
		attachOpen = false;
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
	<!-- Inner container: floating card on padded pages, or a flush full-height panel when
	     docked on the workspace (see surfaceClass). -->
	<div class="flex flex-1 flex-col overflow-hidden {surfaceClass}">
		{#if showList}
			<ChatConversationList />
		{:else}
			<!-- Active Chat Header -->
			<!-- h-11 (not py-3) so this header's divider lines up with the workspace panels'
			     h-11 headers when docked flush; harmless 44px header on the floating card too. -->
			<div class="flex h-11 shrink-0 items-center justify-between border-b {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700'} px-4">
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
					{:else if inDocMode && !$activeConversationId}
						<h3 class="truncate text-laya-base font-semibold">
							{attachedIds.length === 1 ? 'Chat about this document' : `Chat about ${attachedIds.length} documents`}
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
					<div class="group/exp relative">
						<button
							onclick={() => chatExpanded.set(!$chatExpanded)}
							aria-label={$chatExpanded ? 'Collapse chat' : 'Expand chat'}
							class="shrink-0 rounded-md p-1 text-surface-400 transition-colors hover:text-surface-200"
						>
							{#if $chatExpanded}
								<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 9h5V4M20 9h-5V4M4 15h5v5M20 15h-5v5" />
								</svg>
							{:else}
								<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5" />
								</svg>
							{/if}
						</button>
						<span class="pointer-events-none absolute right-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-transparent glass-tooltip glass-tooltip-dense px-2 py-1 text-[10px] font-medium shadow-lg opacity-0 transition-opacity duration-75 group-hover/exp:opacity-100">
							{$chatExpanded ? 'Collapse' : 'Expand'}
						</span>
					</div>
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
						{:else if inDocMode}
							Ask anything about {attachedIds.length === 1 ? 'this document' : `these ${attachedIds.length} documents`}. Laya reads them page by page and cites what it finds.
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
				{#if $chatSending && !$streamingMessageId && $activeTools.length === 0}
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
				{#if attachedIds.length > 0}
					<!-- Attached Corpus documents -->
					<div class="mb-2 flex flex-wrap gap-1.5">
						{#each attachedIds as id, i (id)}
							{@const doc = attachedDocs[i]}
							<span class="inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 text-laya-micro {$glassTheme ? 'border-white/[0.08] bg-white/[0.05]' : 'border-surface-600 bg-surface-800'} text-surface-300">
								<svg class="h-3 w-3 shrink-0 text-surface-500" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6M7 4h7l5 5v11a1 1 0 01-1 1H7a1 1 0 01-1-1V5a1 1 0 011-1z" /></svg>
								<button onclick={() => goto(`/corpus?doc=${encodeURIComponent(id)}`)} class="truncate hover:text-laya-orange" title={doc?.title ?? id}>{doc?.title ?? id}</button>
								{#if doc && doc.status !== 'ready'}
									<span class="shrink-0 {doc.status === 'failed' ? 'text-red-400' : 'text-laya-orange'}">{statusLabel(doc.status)}</span>
								{/if}
								<button onclick={() => detachDocumentId(id)} aria-label="Remove attachment" class="shrink-0 rounded text-surface-500 hover:text-red-400">
									<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
								</button>
							</span>
						{/each}
					</div>
				{/if}
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
					<!-- Attach a Corpus document (disabled, never hidden, while a reply is in flight) -->
					<button
						onclick={() => (attachOpen = !attachOpen)}
						disabled={chatBusy || inCardMode}
						aria-label="Attach documents"
						title={inCardMode ? 'Not available in a card chat' : 'Attach documents'}
						class="absolute bottom-2 right-9 rounded-md p-1 transition-colors disabled:opacity-30 {attachedIds.length > 0 || attachOpen ? 'text-laya-orange' : 'text-surface-500 hover:text-surface-300'}"
					>
						<svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" /></svg>
					</button>
					<button
						onclick={send}
						disabled={!input.trim() || chatBusy}
						aria-label="Send message"
						class="absolute bottom-2 right-2 rounded-md p-1 transition-colors disabled:opacity-30
							{input.trim() ? 'text-laya-orange hover:text-laya-peach' : 'text-surface-600'}"
					>
						<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M12 5l7 7-7 7" />
						</svg>
					</button>

					{#if attachOpen}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div class="fixed inset-0 z-40" onclick={() => (attachOpen = false)} onkeydown={() => {}}></div>
						<div class="absolute bottom-full right-0 z-50 mb-2 flex max-h-72 w-80 flex-col rounded-lg shadow-xl {$glassTheme ? 'glass-section' : 'border border-surface-600 bg-surface-800'}">
							<div class="flex items-center gap-2 border-b px-3 py-2 {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700'}">
								<input
									bind:value={attachFilter}
									placeholder="Filter documents"
									class="min-w-0 flex-1 bg-transparent text-laya-secondary text-surface-200 placeholder-surface-500 focus:outline-none"
								/>
								<button onclick={addFileToChat} disabled={attachBusy} class="shrink-0 text-laya-micro text-laya-orange hover:text-laya-gold disabled:opacity-50">Add file</button>
							</div>
							<div class="flex-1 overflow-auto p-1">
								{#if attachCandidates.length === 0}
									<p class="px-2 py-3 text-center text-laya-micro text-surface-500">
										{$readyDocuments.length === 0 ? 'No documents yet. Drop a file here or press Add file.' : 'No matches'}
									</p>
								{:else}
									{#each attachCandidates as d (d.document_id)}
										<button
											onclick={() => toggleAttach(d.document_id)}
											class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-laya-secondary transition-colors {$glassTheme ? 'glass-hover' : 'hover:bg-surface-700'}"
										>
											<span class="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border {attachedIds.includes(d.document_id) ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-600'}">
												{#if attachedIds.includes(d.document_id)}
													<svg class="h-2.5 w-2.5 text-laya-orange" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg>
												{/if}
											</span>
											<span class="min-w-0 flex-1 truncate text-surface-200">{d.title}</span>
											<span class="shrink-0 text-[9px] font-semibold uppercase text-surface-500">{d.file_type}</span>
										</button>
									{/each}
								{/if}
							</div>
						</div>
					{/if}
				</div>
				<input bind:this={attachFileInput} type="file" multiple class="hidden" onchange={handleAttachFilePicked} />
			</div>
		{/if}
	</div>
	</aside>
{/if}
