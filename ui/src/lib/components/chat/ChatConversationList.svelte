<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import {
		conversations,
		activeConversationId,
		chatListOpen,
		chatMessages,
		chatOpen,
		chatExpanded
	} from '$lib/stores/chat';
	import type { Conversation } from '$lib/api/types';
	import { glassTheme } from '$lib/stores/glassTheme';

	let loading = $state(true);
	let deletingId = $state<string | null>(null);
	let editingId = $state<string | null>(null);
	let editTitle = $state('');
	let editInputEl = $state<HTMLInputElement | undefined>();

	async function loadConversations() {
		loading = true;
		try {
			const list = await engineApi.getConversations(100);
			conversations.set(list);
		} catch {
			// silently fail
		}
		loading = false;
	}

	// Load on mount
	$effect(() => {
		loadConversations();
	});

	// Focus + select the rename input when edit mode opens
	$effect(() => {
		if (editingId && editInputEl) {
			editInputEl.focus();
			editInputEl.select();
		}
	});

	async function openConversation(conv: Conversation) {
		// Don't navigate away while the user is renaming this row
		if (editingId === conv.conversation_id) return;
		activeConversationId.set(conv.conversation_id);
		chatListOpen.set(false);
		// Load messages for this conversation
		try {
			const msgs = await engineApi.getConversationMessages(conv.conversation_id, 50);
			chatMessages.set(msgs.reverse());
		} catch {
			chatMessages.set([]);
		}
	}

	function startEdit(e: Event, conv: Conversation) {
		e.stopPropagation();
		editingId = conv.conversation_id;
		editTitle = conv.title;
	}

	async function commitEdit(convId: string) {
		const trimmed = editTitle.trim().slice(0, 100);
		if (editingId !== convId) return;
		editingId = null;
		if (!trimmed) return;

		// Grab the prior title so we can revert if the API call fails
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

	function cancelEdit() {
		editingId = null;
	}

	function handleEditKey(e: KeyboardEvent, convId: string) {
		if (e.key === 'Enter') {
			e.preventDefault();
			commitEdit(convId);
		} else if (e.key === 'Escape') {
			e.preventDefault();
			cancelEdit();
		}
	}

	async function startNewChat() {
		try {
			const conv = await engineApi.createConversation();
			conversations.update((list) => [conv, ...list]);
			activeConversationId.set(conv.conversation_id);
			chatMessages.set([]);
			chatListOpen.set(false);
		} catch {
			// fallback: just open empty chat with no conversation (will auto-create on first message)
			activeConversationId.set(null);
			chatMessages.set([]);
			chatListOpen.set(false);
		}
	}

	async function deleteConversation(e: Event, convId: string) {
		e.stopPropagation();
		if (deletingId === convId) {
			// Second click = confirm
			try {
				await engineApi.deleteConversation(convId);
				conversations.update((list) => list.filter((c) => c.conversation_id !== convId));
				// If we deleted the active conversation, clear it
				activeConversationId.update((id) => (id === convId ? null : id));
			} catch {
				// ignore
			}
			deletingId = null;
		} else {
			deletingId = convId;
			// Reset after 3 seconds if not confirmed
			setTimeout(() => {
				if (deletingId === convId) deletingId = null;
			}, 3000);
		}
	}

	function relativeTime(ts: string): string {
		if (!ts) return '';
		const diff = Date.now() - new Date(ts).getTime();
		const mins = Math.floor(diff / 60000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hours = Math.floor(mins / 60);
		if (hours < 24) return `${hours}h ago`;
		const days = Math.floor(hours / 24);
		if (days === 1) return 'yesterday';
		if (days < 30) return `${days}d ago`;
		return new Date(ts).toLocaleDateString();
	}
</script>

<div class="flex h-full flex-col">
	<!-- Header -->
	<div class="flex items-center justify-between border-b {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700'} px-4 py-3">
		<h3 class="text-laya-base font-semibold">Conversations</h3>
		<div class="flex items-center gap-1">
			<!-- New Chat -->
			<button
				onclick={startNewChat}
				aria-label="New chat"
				class="rounded-md p-1 text-surface-400 transition-colors {$glassTheme ? 'glass-hover' : 'hover:bg-surface-800'} hover:text-laya-orange"
				title="New chat"
			>
				<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
				</svg>
			</button>
			<!-- Expand / collapse the wide overlay -->
			<button
				onclick={() => chatExpanded.set(!$chatExpanded)}
				aria-label={$chatExpanded ? 'Collapse chat' : 'Expand chat'}
				title={$chatExpanded ? 'Collapse' : 'Expand'}
				class="rounded-md p-1 text-surface-400 transition-colors {$glassTheme ? 'glass-hover' : 'hover:bg-surface-800'} hover:text-laya-orange"
			>
				{#if $chatExpanded}
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 9L4 4m0 0v4m0-4h4m7 5l5-5m0 0v4m0-4h-4M9 15l-5 5m0 0v-4m0 4h4m7-5l5 5m0 0v-4m0 4h-4" />
					</svg>
				{:else}
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
					</svg>
				{/if}
			</button>
			<!-- Close -->
			<button
				onclick={() => chatOpen.set(false)}
				aria-label="Close chat"
				class="rounded-md p-1 text-surface-400 transition-colors hover:text-surface-200"
			>
				<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>
	</div>

	<!-- List -->
	<div class="flex-1 overflow-auto">
		{#if loading}
			<p class="p-4 text-center text-laya-base text-surface-500">Loading...</p>
		{:else if $conversations.length === 0}
			<div class="flex flex-col items-center gap-3 p-8 text-center">
				<svg class="h-10 w-10 text-surface-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
				</svg>
				<p class="text-laya-base text-surface-500">No conversations yet</p>
				<button
					onclick={startNewChat}
					class="rounded-md bg-laya-orange/80 px-3 py-1.5 text-laya-secondary font-medium text-surface-900 transition-colors hover:bg-laya-orange"
				>
					Start a new chat
				</button>
			</div>
		{:else}
			<div class="p-2">
				{#each $conversations as conv (conv.conversation_id)}
					<div
						role="button"
						tabindex="0"
						onclick={() => openConversation(conv)}
						onkeydown={(e) => { if (e.key === 'Enter') openConversation(conv); }}
						class="group w-full cursor-pointer rounded-lg px-3 py-2.5 text-left transition-colors {$glassTheme ? 'glass-hover' : 'hover:bg-surface-800'}"
					>
						<div class="flex items-center justify-between gap-2">
							{#if editingId === conv.conversation_id}
								<input
									bind:this={editInputEl}
									bind:value={editTitle}
									onclick={(e) => e.stopPropagation()}
									onkeydown={(e) => handleEditKey(e, conv.conversation_id)}
									onblur={() => commitEdit(conv.conversation_id)}
									maxlength={100}
									aria-label="Rename conversation"
									class="min-w-0 flex-1 rounded border border-laya-orange/40 {$glassTheme ? 'bg-white/[0.05]' : 'bg-surface-800'} px-1.5 py-0.5 text-laya-base font-medium text-surface-100 focus:border-laya-orange focus:outline-none"
								/>
							{:else}
								<span class="min-w-0 flex-1 truncate text-laya-base font-medium text-surface-200">{conv.title}</span>
								<!-- Right slot swaps timestamp ↔ actions on hover so the title can span
									 the full width (no permanently reserved action column). The timestamp
									 stays in normal flow and defines the slot's width (min-w guarantees the
									 actions fit), so nothing reflows on hover (no jitter). The actions are
									 absolutely positioned over the slot so each button's hitbox lines up
									 exactly with its icon. While a delete is pending the actions stay shown
									 even without hover. -->
								<div class="relative flex min-w-12 shrink-0 items-center justify-end">
									<span class="text-laya-micro text-surface-500 transition-opacity {deletingId === conv.conversation_id ? 'opacity-0' : 'group-hover:opacity-0'}">{relativeTime(conv.updated_at)}</span>
									<div class="absolute right-0 top-1/2 flex -translate-y-1/2 items-center gap-0.5 transition-opacity {deletingId === conv.conversation_id ? 'opacity-100' : 'pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100'}">
										<!-- Rename -->
										<button
											onclick={(e) => startEdit(e, conv)}
											class="rounded p-1 text-surface-500 transition-colors hover:text-laya-orange"
											aria-label="Rename conversation"
											title="Rename"
										>
											<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
											</svg>
										</button>
										<!-- Delete -->
										<div class="group/del relative">
											<button
												onclick={(e) => deleteConversation(e, conv.conversation_id)}
												class="rounded p-1 transition-colors {deletingId === conv.conversation_id ? 'text-red-400' : 'text-surface-500 hover:text-red-400'}"
												aria-label={deletingId === conv.conversation_id ? 'Click again to confirm delete' : 'Double-click to delete'}
											>
												<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
													<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
												</svg>
											</button>
											<span class="pointer-events-none absolute right-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-transparent glass-tooltip glass-tooltip-dense px-2 py-1 text-[10px] font-medium shadow-lg
												opacity-0 transition-opacity duration-75 group-hover/del:opacity-100
												{deletingId === conv.conversation_id ? '!border-red-400/30 !bg-red-950 !text-red-300' : ''}">
												{deletingId === conv.conversation_id ? 'Click again to confirm' : 'Double-click to delete'}
											</span>
										</div>
									</div>
								</div>
							{/if}
						</div>
						{#if conv.preview}
							<p class="mt-0.5 truncate text-laya-secondary text-surface-500">{conv.preview}</p>
						{/if}
						<span class="mt-0.5 text-laya-micro text-surface-600">{conv.message_count} message{conv.message_count === 1 ? '' : 's'}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
