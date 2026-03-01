<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import type { ActionCard, CardGroup } from '$lib/api/types';
	import CardGroupComponent from '$lib/components/feed/CardGroup.svelte';
	import ActionCardComponent from '$lib/components/feed/ActionCard.svelte';
	import CardDetail from '$lib/components/feed/CardDetail.svelte';

	let groups = $state<CardGroup[]>([]);
	let totalGroups = $state(0);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let selectedCard = $state<ActionCard | null>(null);

	// Filters & sort
	let statusFilter = $state('');
	let priorityFilter = $state('');
	let sortBy = $state('newest');
	let showArchived = $state(false);

	async function loadGroups() {
		loading = true;
		error = null;
		try {
			const data = await engineApi.getGroupedCards({
				status: statusFilter || undefined,
				priority: priorityFilter || undefined,
				sort: sortBy,
				show_archived: showArchived || undefined
			});
			groups = data.groups;
			totalGroups = data.total_groups;
		} catch {
			error = 'Failed to load cards';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		loadGroups();
	});

	$effect(() => {
		statusFilter;
		priorityFilter;
		sortBy;
		showArchived;
		loadGroups();
	});

	// WebSocket: reload on new card, patch status on update
	$effect(() => {
		const msg = $lastMessage;
		if (!msg) return;

		if (msg.type === 'card_created') {
			loadGroups();
		} else if (msg.type === 'card_updated' && msg.card_id) {
			const payload = msg.payload as { status?: string };
			if (!payload.status) return;

			// If card is archived and we're not showing archived, reload to remove it
			if (payload.status === 'archived' && !showArchived) {
				loadGroups();
				return;
			}

			for (const group of groups) {
				const card = group.cards.find((c) => c.card_id === msg.card_id);
				if (card) {
					card.status = payload.status as ActionCard['status'];
					if (selectedCard?.card_id === msg.card_id) {
						selectedCard.status = payload.status as ActionCard['status'];
					}
					group.has_pending = group.cards.some((c) => c.status === 'pending');
					break;
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

	const totalCards = $derived(groups.reduce((sum, g) => sum + g.card_count, 0));

	// Responsive masonry: measure container, compute how many 320px cols fit
	const CARD_WIDTH = 320;
	const COL_GAP = 16; // gap-4 = 1rem = 16px

	let gridEl = $state<HTMLElement | null>(null);
	let numColumns = $state(3);

	$effect(() => {
		if (!gridEl) return;
		const observer = new ResizeObserver(([entry]) => {
			const w = entry.contentRect.width;
			// N cols of 320px + (N-1) gaps of 16px → N = floor((w + gap) / (card + gap))
			numColumns = Math.max(1, Math.floor((w + COL_GAP) / (CARD_WIDTH + COL_GAP)));
		});
		observer.observe(gridEl);
		return () => observer.disconnect();
	});

	// Round-robin distribution into however many columns fit
	const columns = $derived(
		Array.from(
			{ length: Math.min(numColumns, Math.max(1, groups.length)) },
			(_, col) => groups.filter((_, i) => i % Math.min(numColumns, Math.max(1, groups.length)) === col)
		)
	);
</script>

<div class="flex h-full gap-4">
	<!-- Group list -->
	<div class="flex min-w-0 flex-1 flex-col">
		<!-- Filter / sort bar -->
		<div class="mb-4 flex flex-wrap items-center gap-3">
			<h2 class="text-lg font-semibold">Feed</h2>
			<span class="text-sm text-surface-500">
				{totalGroups} {totalGroups === 1 ? 'group' : 'groups'} · {totalCards} cards
			</span>

			<div class="ml-auto flex flex-wrap items-center gap-2">
				<!-- Sort -->
				<select
					bind:value={sortBy}
					class="rounded-lg border border-surface-600 bg-surface-800 px-2 py-1.5 text-xs text-surface-200"
				>
					<option value="newest">New</option>
					<option value="priority">Priority</option>
					<option value="category">Category</option>
					<option value="platform">Event Source</option>
				</select>

				<!-- Status filter -->
				<select
					bind:value={statusFilter}
					class="rounded-lg border border-surface-600 bg-surface-800 px-2 py-1.5 text-xs text-surface-200"
				>
					<option value="">All statuses</option>
					<option value="pending">Pending</option>
					<option value="approved">Approved</option>
					<option value="dismissed">Dismissed</option>
					<option value="archived">Archived</option>
				</select>

				<!-- Priority filter -->
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

				<!-- Show archived toggle -->
				<label class="flex cursor-pointer items-center gap-1.5 text-xs text-surface-400">
					<input
						type="checkbox"
						bind:checked={showArchived}
						class="h-3.5 w-3.5 rounded border-surface-600 bg-surface-800"
					/>
					Show archived
				</label>
			</div>
		</div>

		<!-- Groups — 3 explicit flex columns for compact masonry (no row-height gaps) -->
		{#if loading && groups.length === 0}
			<div class="py-12 text-center text-surface-400">Loading cards...</div>
		{:else if error}
			<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-3 text-sm text-red-300">
				{error}
			</div>
		{:else if groups.length === 0}
			<div class="py-12 text-center text-surface-500">
				<p class="text-lg">No action cards yet</p>
				<p class="mt-1 text-sm">Cards will appear here as events are processed</p>
			</div>
		{:else}
			<div bind:this={gridEl} class="flex gap-4">
				{#each columns as col}
					<div class="flex w-[320px] shrink-0 flex-col gap-4">
						{#each col as group (group.entity_id)}
							{#if group.card_count === 1}
								<ActionCardComponent card={group.cards[0]} onselect={selectCard} />
							{:else}
								<CardGroupComponent {group} onselect={selectCard} />
							{/if}
						{/each}
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Detail panel -->
	{#if selectedCard}
		<div class="w-[420px] flex-shrink-0">
			<CardDetail card={selectedCard} onclose={closeDetail} />
		</div>
	{/if}
</div>
