<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import type { ActionCard } from '$lib/api/types';
	import ActionCardComponent from '$lib/components/feed/ActionCard.svelte';
	import CardDetail from '$lib/components/feed/CardDetail.svelte';

	let cards = $state<ActionCard[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let selectedCard = $state<ActionCard | null>(null);

	// Filters
	let statusFilter = $state('');
	let priorityFilter = $state('');
	let sortBy = $state('created_at_desc');

	async function loadCards() {
		loading = true;
		error = null;
		try {
			const data = await engineApi.getCards({
				status: statusFilter || undefined,
				priority: priorityFilter || undefined,
				sort: sortBy,
				limit: 50
			});
			cards = data.cards;
			total = data.total;
		} catch {
			error = 'Failed to load cards';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		loadCards();
	});

	// Reload when filters change
	$effect(() => {
		// Access reactive values to create dependency
		statusFilter;
		priorityFilter;
		sortBy;
		loadCards();
	});

	// Listen for WS updates
	$effect(() => {
		const msg = $lastMessage;
		if (!msg) return;

		if (msg.type === 'card_created') {
			loadCards();
		} else if (msg.type === 'card_updated' && msg.card_id) {
			const payload = msg.payload as { status?: string };
			const idx = cards.findIndex((c) => c.card_id === msg.card_id);
			if (idx !== -1 && payload.status) {
				cards[idx].status = payload.status as ActionCard['status'];
				if (selectedCard?.card_id === msg.card_id) {
					selectedCard.status = payload.status as ActionCard['status'];
				}
			}
		}
	});

	function selectCard(card: ActionCard) {
		selectedCard = card;
	}

	function closeDetail() {
		selectedCard = null;
	}
</script>

<div class="flex h-full gap-4">
	<!-- Card list -->
	<div class="flex min-w-0 flex-1 flex-col">
		<!-- Filter bar -->
		<div class="mb-4 flex items-center gap-3">
			<h2 class="text-lg font-semibold">Feed</h2>
			<span class="text-sm text-surface-500">{total} cards</span>

			<div class="ml-auto flex gap-2">
				<select
					bind:value={statusFilter}
					class="rounded-lg border border-surface-600 bg-surface-800 px-2 py-1.5 text-xs text-surface-200"
				>
					<option value="">All statuses</option>
					<option value="pending">Pending</option>
					<option value="approved">Approved</option>
					<option value="dismissed">Dismissed</option>
				</select>

				<select
					bind:value={priorityFilter}
					class="rounded-lg border border-surface-600 bg-surface-800 px-2 py-1.5 text-xs text-surface-200"
				>
					<option value="">All priorities</option>
					<option value="CRITICAL">Critical</option>
					<option value="HIGH">High</option>
					<option value="MEDIUM">Medium</option>
					<option value="LOW">Low</option>
				</select>

				<select
					bind:value={sortBy}
					class="rounded-lg border border-surface-600 bg-surface-800 px-2 py-1.5 text-xs text-surface-200"
				>
					<option value="created_at_desc">Newest first</option>
					<option value="created_at_asc">Oldest first</option>
					<option value="priority_desc">Priority</option>
				</select>
			</div>
		</div>

		<!-- Cards grid -->
		{#if loading && cards.length === 0}
			<div class="py-12 text-center text-surface-400">Loading cards...</div>
		{:else if error}
			<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-3 text-sm text-red-300">{error}</div>
		{:else if cards.length === 0}
			<div class="py-12 text-center text-surface-500">
				<p class="text-lg">No action cards yet</p>
				<p class="mt-1 text-sm">Cards will appear here as events are processed</p>
			</div>
		{:else}
			<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
				{#each cards as card (card.card_id)}
					<ActionCardComponent {card} onselect={selectCard} />
				{/each}
			</div>
		{/if}
	</div>

	<!-- Detail panel (slide-over) -->
	{#if selectedCard}
		<div class="w-[420px] flex-shrink-0">
			<CardDetail card={selectedCard} onclose={closeDetail} />
		</div>
	{/if}
</div>
