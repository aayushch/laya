<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import {
		conversations,
		activeConversationId,
		chatListOpen,
		chatMessages,
		chatOpen
	} from '$lib/stores/chat';
	import type { Conversation } from '$lib/api/types';

	let loading = $state(true);
	let deletingId = $state<string | null>(null);

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

	async function openConversation(conv: Conversation) {
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
	<div class="flex items-center justify-between border-b border-surface-700 px-4 py-3">
		<h3 class="text-sm font-semibold">Conversations</h3>
		<div class="flex items-center gap-1">
			<!-- New Chat -->
			<button
				onclick={startNewChat}
				aria-label="New chat"
				class="rounded-md p-1 text-surface-400 transition-colors hover:bg-surface-800 hover:text-laya-orange"
				title="New chat"
			>
				<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
				</svg>
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
			<p class="p-4 text-center text-sm text-surface-500">Loading...</p>
		{:else if $conversations.length === 0}
			<div class="flex flex-col items-center gap-3 p-8 text-center">
				<svg class="h-10 w-10 text-surface-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
				</svg>
				<p class="text-sm text-surface-500">No conversations yet</p>
				<button
					onclick={startNewChat}
					class="rounded-md bg-laya-orange/80 px-3 py-1.5 text-xs font-medium text-surface-900 transition-colors hover:bg-laya-orange"
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
						class="group flex w-full cursor-pointer items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-surface-800"
					>
						<div class="min-w-0 flex-1">
							<div class="flex items-center justify-between gap-2">
								<span class="truncate text-sm font-medium text-surface-200">{conv.title}</span>
								<span class="shrink-0 text-[10px] text-surface-500">{relativeTime(conv.updated_at)}</span>
							</div>
							{#if conv.preview}
								<p class="mt-0.5 truncate text-xs text-surface-500">{conv.preview}</p>
							{/if}
							<span class="mt-0.5 text-[10px] text-surface-600">{conv.message_count} message{conv.message_count === 1 ? '' : 's'}</span>
						</div>
						<!-- Delete -->
						<div class="group/del relative shrink-0">
							<button
								onclick={(e) => deleteConversation(e, conv.conversation_id)}
								class="rounded p-1 text-surface-600 opacity-0 transition-all hover:text-red-400 group-hover:opacity-100
									{deletingId === conv.conversation_id ? '!opacity-100 !text-red-400' : ''}"
								aria-label={deletingId === conv.conversation_id ? 'Click again to confirm delete' : 'Double-click to delete'}
							>
								<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
								</svg>
							</button>
							<span class="pointer-events-none absolute right-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border px-2 py-1 text-[10px] font-medium shadow-lg
								opacity-0 transition-opacity duration-75 group-hover/del:opacity-100
								{deletingId === conv.conversation_id
									? 'border-red-400/30 bg-red-950 text-red-300'
									: 'border-surface-600 bg-surface-800 text-surface-400'}">
								{deletingId === conv.conversation_id ? 'Click again to confirm' : 'Double-click to delete'}
							</span>
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>
