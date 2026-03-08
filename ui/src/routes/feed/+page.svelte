<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import { feedFilters, feedDate, feedPrevDate, feedNextDate } from '$lib/stores/feedFilters';
	import type { ActionCard, CardGroup } from '$lib/api/types';
	import CardGroupComponent from '$lib/components/feed/CardGroup.svelte';
	import ActionCardComponent from '$lib/components/feed/ActionCard.svelte';
	import CardDetail from '$lib/components/feed/CardDetail.svelte';

	let groups = $state<CardGroup[]>([]);
	let totalGroups = $state(0);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let selectedCard = $state<ActionCard | null>(null);

	function formatDateLabel(dateStr: string): string {
		const today = new Date().toISOString().slice(0, 10);
		const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
		if (dateStr === today) return 'Today';
		if (dateStr === yesterday) return 'Yesterday';
		return new Date(dateStr + 'T00:00:00').toLocaleDateString(undefined, {
			weekday: 'short',
			month: 'short',
			day: 'numeric'
		});
	}

	const isToday = $derived($feedDate === new Date().toISOString().slice(0, 10));

	let _fetchId = 0;
	let _reloadTimer: ReturnType<typeof setTimeout> | null = null;

	async function loadGroups() {
		const id = ++_fetchId;
		loading = true;
		error = null;
		try {
			const f = $feedFilters;
			const data = await engineApi.getGroupedCards({
				status: f.statusFilters.length ? f.statusFilters.join(',') : undefined,
				priority: f.priorityFilters.length ? f.priorityFilters.join(',') : undefined,
				sort: f.sortBy,
				show_archived: f.showArchived || undefined,
				date: $feedDate
			});
			if (id !== _fetchId) return;
			groups = data.groups;
			totalGroups = data.total_groups;
			$feedPrevDate = data.prev_date ?? null;
			$feedNextDate = data.next_date ?? null;
		} catch {
			if (id !== _fetchId) return;
			error = 'Failed to load cards';
		} finally {
			if (id === _fetchId) loading = false;
		}
	}

	function scheduleReload() {
		if (_reloadTimer) clearTimeout(_reloadTimer);
		_reloadTimer = setTimeout(() => {
			_reloadTimer = null;
			loadGroups();
		}, 300);
	}

	$effect(() => {
		$feedDate;
		$feedFilters;
		loadGroups();
	});

	// Track last processed WS message to prevent infinite re-triggering.
	// This $effect reads groups/selectedCard (to modify them), so when those
	// change the effect re-runs — without dedup it processes the same message
	// again, modifies groups again, re-triggers again → infinite loop.
	let _lastProcessedMsg: unknown = null;

	// WebSocket handlers
	$effect(() => {
		const msg = $lastMessage;
		if (!msg) return;
		if (msg === _lastProcessedMsg) return;
		if (!['card_created', 'card_deleted', 'card_updated'].includes(msg.type)) return;
		_lastProcessedMsg = msg;

		if (msg.type === 'card_created') {
			// Only auto-reload if viewing today (new cards land on today)
			if (isToday) scheduleReload();
		} else if (msg.type === 'card_deleted' && msg.card_id) {
			if (selectedCard?.card_id === msg.card_id) selectedCard = null;
			groups = groups
				.map((g) => ({ ...g, cards: g.cards.filter((c) => c.card_id !== msg.card_id) }))
				.filter((g) => g.cards.length > 0);
			totalGroups = groups.length;
		} else if (msg.type === 'card_updated' && msg.card_id) {
			const payload = msg.payload as {
				status?: string;
				header?: string;
				summary?: string;
				priority?: string;
				persona?: string;
				category?: string;
				has_workspace?: boolean;
				selected_action_id?: string;
			};
			if (!payload.status) return;

			if (payload.status === 'archived' && !$feedFilters.showArchived) {
				scheduleReload();
				return;
			}

			for (const group of groups) {
				const card = group.cards.find((c) => c.card_id === msg.card_id);
				if (card) {
					card.status = payload.status as ActionCard['status'];
					if (payload.header) card.header = payload.header;
					if (payload.summary) card.summary = payload.summary;
					if (payload.priority) card.priority = payload.priority as ActionCard['priority'];
					if (payload.persona) card.persona = payload.persona as ActionCard['persona'];
					if (payload.has_workspace !== undefined) card.has_workspace = payload.has_workspace;
					if (payload.selected_action_id) card.selected_action_id = payload.selected_action_id;
					if (selectedCard?.card_id === msg.card_id) {
						Object.assign(selectedCard, { status: card.status, header: card.header, summary: card.summary, selected_action_id: card.selected_action_id });
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

	function handleDelete(cardId: string) {
		if (selectedCard?.card_id === cardId) selectedCard = null;
		groups = groups
			.map((g) => ({ ...g, cards: g.cards.filter((c) => c.card_id !== cardId) }))
			.filter((g) => g.cards.length > 0);
		totalGroups = groups.length;
	}

	const totalCards = $derived(groups.reduce((sum, g) => sum + g.card_count, 0));

	// Responsive masonry
	const CARD_WIDTH = 320;
	const COL_GAP = 16;

	let containerEl = $state<HTMLElement | null>(null);
	let numColumns = $state(3);

	$effect(() => {
		if (!containerEl) return;
		const observer = new ResizeObserver(([entry]) => {
			const w = entry.contentRect.width;
			numColumns = Math.max(1, Math.floor((w + COL_GAP) / (CARD_WIDTH + COL_GAP)));
		});
		observer.observe(containerEl);
		return () => observer.disconnect();
	});

	// Distribute groups into columns (round-robin)
	function toColumns(items: CardGroup[]): CardGroup[][] {
		const cols = Math.min(numColumns, Math.max(1, items.length));
		return Array.from({ length: cols }, (_, col) =>
			items.filter((_, i) => i % cols === col)
		);
	}

	// Default columns for non-sorted view
	const columns = $derived(toColumns(groups));

	// Sort sections: group by sort_key when a non-date sort is active
	const sections = $derived.by(() => {
		if ($feedFilters.sortBy === 'newest' || $feedFilters.sortBy === 'oldest') return null;
		const map = new Map<string, CardGroup[]>();
		for (const g of groups) {
			const key = g.sort_key ?? 'Other';
			if (!map.has(key)) map.set(key, []);
			map.get(key)!.push(g);
		}
		return [...map.entries()];
	});

	// Collapsed sections — reset when sort order changes
	let collapsedSections = $state<Set<string>>(new Set());
	$effect(() => {
		$feedFilters.sortBy;
		collapsedSections = new Set();
	});

	function toggleSection(key: string) {
		const next = new Set(collapsedSections);
		if (next.has(key)) next.delete(key); else next.add(key);
		collapsedSections = next;
	}
</script>

<div class="flex h-full gap-4">
	<!-- Cards section -->
	<div bind:this={containerEl} class="flex min-w-0 flex-1 flex-col overflow-y-auto">
		<!-- Summary bar -->
		<div class="mb-3 flex items-center gap-2 pr-3">
			<span class="text-xs text-surface-500">
				{totalGroups} {totalGroups === 1 ? 'group' : 'groups'} · {totalCards} cards
			</span>
		</div>

		<!-- Card grid -->
		{#if loading && groups.length === 0}
			<div class="py-12 text-center text-surface-400">Loading cards...</div>
		{:else if error}
			<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-3 text-sm text-red-300">
				{error}
			</div>
		{:else if groups.length === 0}
			<div class="py-12 text-center text-surface-500">
				<p class="text-lg">No cards for {formatDateLabel($feedDate)}</p>
				<p class="mt-1 text-sm">
					{#if $feedPrevDate}
						<button class="text-laya-orange hover:underline" onclick={() => { if ($feedPrevDate) $feedDate = $feedPrevDate; }}>
							View {formatDateLabel($feedPrevDate)}
						</button>
					{:else}
						Cards will appear here as events are processed
					{/if}
				</p>
			</div>
		{:else if sections}
			<!-- Sorted view with section separators -->
			{#each sections as [sectionTitle, sectionGroups], si}
				{@const isCollapsed = collapsedSections.has(sectionTitle)}
				<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
				<div
					class="flex cursor-pointer items-center gap-3 pr-3 {si > 0 ? 'mt-5' : ''} mb-3 select-none"
					onclick={() => toggleSection(sectionTitle)}
				>
					<svg class="h-3.5 w-3.5 shrink-0 text-surface-500 transition-transform {isCollapsed ? '-rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
					</svg>
					<span class="text-xs font-semibold uppercase tracking-wider text-surface-400">{sectionTitle}</span>
					<div class="flex-1 border-t border-surface-700"></div>
					<span class="text-[10px] text-surface-500">{sectionGroups.reduce((s, g) => s + g.card_count, 0)}</span>
				</div>
				{#if !isCollapsed}
					<div class="flex flex-wrap gap-4">
						{#each toColumns(sectionGroups) as col}
							<div class="flex w-[320px] flex-col gap-4">
								{#each col as group (group.entity_id)}
									{#if group.card_count === 1}
										<ActionCardComponent card={group.cards[0]} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} hasSelection={!!selectedCard} />
									{:else}
										<CardGroupComponent {group} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} hasSelection={!!selectedCard} />
									{/if}
								{/each}
							</div>
						{/each}
					</div>
				{/if}
			{/each}
		{:else}
			<!-- Default column layout (newest / oldest) -->
			<div class="flex flex-wrap gap-4">
				{#each columns as col}
					<div class="flex w-[320px] flex-col gap-4">
						{#each col as group (group.entity_id)}
							{#if group.card_count === 1}
								<ActionCardComponent card={group.cards[0]} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} hasSelection={!!selectedCard} />
							{:else}
								<CardGroupComponent {group} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} hasSelection={!!selectedCard} />
							{/if}
						{/each}
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Detail panel -->
	<div class="w-[420px] flex-shrink-0 overflow-y-auto">
		{#if selectedCard}
			<CardDetail card={selectedCard} onclose={closeDetail} />
		{:else}
			<div class="flex h-full flex-col items-center justify-center rounded-xl border border-dashed border-surface-700 text-surface-600">
				<svg class="mb-2 h-8 w-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
				</svg>
				<p class="text-xs">Select a card to view details</p>
			</div>
		{/if}
	</div>
</div>
