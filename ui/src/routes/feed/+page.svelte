<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import { feedFilters, feedDate, feedPrevDate, feedNextDate } from '$lib/stores/feedFilters';
	import type { ActionCard, CardGroup, DaySummary } from '$lib/api/types';
	import CardGroupComponent from '$lib/components/feed/CardGroup.svelte';
	import ActionCardComponent from '$lib/components/feed/ActionCard.svelte';
	import CardDetail from '$lib/components/feed/CardDetail.svelte';
	import DaySummaryComponent from '$lib/components/feed/DaySummary.svelte';

	let groups = $state<CardGroup[]>([]);
	let totalGroups = $state(0);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let selectedCard = $state<ActionCard | null>(null);

	// Summary View state — persisted to localStorage
	const SUMMARY_VIEW_KEY = 'laya_feed_summary_view';
	let showSummary = $state(
		typeof window !== 'undefined' && localStorage.getItem(SUMMARY_VIEW_KEY) === 'true'
	);
	let daySummary = $state<DaySummary | null>(null);
	let summaryUpdatedAt = $state<string | null>(null);
	let summaryLoading = $state(false);

	function toggleSummaryView() {
		showSummary = !showSummary;
		localStorage.setItem(SUMMARY_VIEW_KEY, String(showSummary));
		if (showSummary && !daySummary) loadSummary();
	}

	// Persist selected card ID across webview navigations (e.g. external link → back)
	const SELECTED_CARD_KEY = 'laya_feed_selected_card';

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

			// Restore selected card after navigating back from an external page
			if (!selectedCard) {
				const savedId = sessionStorage.getItem(SELECTED_CARD_KEY);
				if (savedId) {
					for (const g of data.groups) {
						const found = g.cards.find((c) => c.card_id === savedId);
						if (found) { selectedCard = found; break; }
					}
				}
			}

			// Scroll to card if gotoCard was triggered (e.g. after date navigation)
			if (_scrollToCardId) {
				scrollToCard(_scrollToCardId);
			}
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

	async function loadSummary() {
		summaryLoading = true;
		try {
			const data = await engineApi.getDaySummary($feedDate);
			daySummary = data.summary;
			summaryUpdatedAt = data.updated_at;
		} catch {
			daySummary = null;
			summaryUpdatedAt = null;
		} finally {
			summaryLoading = false;
		}
	}

	$effect(() => {
		$feedDate;
		$feedFilters;
		loadGroups();
	});

	// Load summary when date changes or summary view is toggled on
	$effect(() => {
		$feedDate;
		if (showSummary) loadSummary();
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

		// Handle summary updates
		if (msg.type === 'summary_updated' && msg.payload) {
			_lastProcessedMsg = msg;
			const payload = msg.payload as { date?: string; summary?: DaySummary; updated_at?: string };
			if (payload.date === $feedDate && payload.summary) {
				daySummary = payload.summary;
				summaryUpdatedAt = payload.updated_at ?? null;
			}
			return;
		}

		if (!['card_created', 'card_deleted', 'card_updated'].includes(msg.type)) return;
		_lastProcessedMsg = msg;

		if (msg.type === 'card_created') {
			// Only auto-reload if viewing today (new cards land on today)
			if (isToday) scheduleReload();
		} else if (msg.type === 'card_deleted' && msg.card_id) {
			if (selectedCard?.card_id === msg.card_id) {
				selectedCard = null;
				sessionStorage.removeItem(SELECTED_CARD_KEY);
			}
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

	// ID of card to scroll into view after next render (set by gotoCard)
	let _scrollToCardId: string | null = null;

	function gotoCard(card: ActionCard) {
		const cardDate = card.created_at ? card.created_at.slice(0, 10) : null;
		_scrollToCardId = card.card_id;

		if (cardDate && cardDate !== $feedDate) {
			// Navigate to the card's date — loadGroups will fire via the $effect on feedDate
			$feedDate = cardDate;
		} else {
			// Already on the right date, scroll now
			scrollToCard(card.card_id);
		}
	}

	function scrollToCard(cardId: string) {
		// Use tick + rAF to wait for DOM to settle after re-render
		requestAnimationFrame(() => {
			const el = document.querySelector(`[data-card-id="${cardId}"]`);
			if (el) {
				el.scrollIntoView({ behavior: 'smooth', block: 'center' });
				// Brief highlight flash
				el.classList.add('ring-2', 'ring-laya-orange/60');
				setTimeout(() => el.classList.remove('ring-2', 'ring-laya-orange/60'), 1500);
			}
			_scrollToCardId = null;
		});
	}

	function selectCard(card: ActionCard) {
		selectedCard = card;
		sessionStorage.setItem(SELECTED_CARD_KEY, card.card_id);
	}

	function closeDetail() {
		selectedCard = null;
		sessionStorage.removeItem(SELECTED_CARD_KEY);
	}

	function handleDelete(cardId: string) {
		if (selectedCard?.card_id === cardId) {
			selectedCard = null;
			sessionStorage.removeItem(SELECTED_CARD_KEY);
		}
		groups = groups
			.map((g) => ({ ...g, cards: g.cards.filter((c) => c.card_id !== cardId) }))
			.filter((g) => g.cards.length > 0);
		totalGroups = groups.length;
	}

	function handleSummaryGotoCard(cardId: string) {
		// Switch to card view and select + scroll to the card
		showSummary = false;
		// Find the card in groups
		for (const g of groups) {
			const found = g.cards.find((c) => c.card_id === cardId);
			if (found) {
				selectCard(found);
				scrollToCard(cardId);
				return;
			}
		}
		// Card might not be loaded — try fetching it to find its date
		engineApi.getCard(cardId).then((card) => {
			gotoCard(card as ActionCard);
			selectCard(card as ActionCard);
		}).catch(() => {});
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
	<div bind:this={containerEl} class="flex min-w-0 flex-1 flex-col overflow-y-auto pl-0.5">
		<!-- Summary bar -->
		<div class="mb-3 flex items-center gap-2 pr-3">
			<span class="text-xs text-surface-500">
				{totalGroups} {totalGroups === 1 ? 'group' : 'groups'} · {totalCards} cards
			</span>
			<div class="flex-1"></div>
			<button
				class="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors {showSummary ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-400 hover:bg-surface-800 hover:text-surface-200'}"
				onclick={toggleSummaryView}
				title={showSummary ? 'Card View' : 'Summary View'}
			>
				{#if showSummary}
					<!-- Grid icon for "Card View" -->
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
					</svg>
					<span>Card View</span>
				{:else}
					<!-- List/summary icon for "Summary View" -->
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
					</svg>
					<span>Summary</span>
				{/if}
			</button>
		</div>

		<!-- Summary View -->
		{#if showSummary}
			<div class="flex-1 overflow-y-auto rounded-xl border border-surface-700/50 bg-surface-900/30 p-6">
				{#if summaryLoading}
					<div class="flex h-full items-center justify-center text-surface-400">
						<span class="text-sm">Loading summary...</span>
					</div>
				{:else}
					<DaySummaryComponent summary={daySummary} updatedAt={summaryUpdatedAt} ongotocard={handleSummaryGotoCard} />
				{/if}
			</div>
		<!-- Card grid -->
		{:else if loading && groups.length === 0}
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
				<div
					class="flex cursor-pointer items-center gap-3 pr-3 {si > 0 ? 'mt-5' : ''} mb-3 select-none"
					role="button"
					tabindex="0"
					onclick={() => toggleSection(sectionTitle)}
					onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleSection(sectionTitle); } }}
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

	<!-- Detail panel (hidden in summary view) -->
	{#if !showSummary}
		<div class="w-[420px] flex-shrink-0 overflow-y-auto">
			{#if selectedCard}
				<CardDetail card={selectedCard} onclose={closeDetail} ongotocard={gotoCard} />
			{:else}
				<div class="flex h-full flex-col items-center justify-center rounded-xl border border-dashed border-surface-700 text-surface-600">
					<svg class="mb-2 h-8 w-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
					</svg>
					<p class="text-xs">Select a card to view details</p>
				</div>
			{/if}
		</div>
	{/if}
</div>
