<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import { feedFilters, feedDate, feedPrevDate, feedNextDate, localToday } from '$lib/stores/feedFilters';
	import type { ActionCard, CardGroup, DaySummary } from '$lib/api/types';
	import CardGroupComponent from '$lib/components/feed/CardGroup.svelte';
	import ActionCardComponent from '$lib/components/feed/ActionCard.svelte';
	import CardDetail from '$lib/components/feed/CardDetail.svelte';
	import DaySummaryComponent from '$lib/components/feed/DaySummary.svelte';
	import { feedViewMode } from '$lib/stores/feedView';
	import ListRow from '$lib/components/feed/ListRow.svelte';
	import ListGroupComponent from '$lib/components/feed/ListGroup.svelte';
	import { recentCards, recentDrawerOpen, trackCardVisit, clearRecentCards, type RecentCardEntry } from '$lib/stores/recentCards';

	let groups = $state<CardGroup[]>([]);
	let totalGroups = $state(0);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let selectedCard = $state<ActionCard | null>(null);

	// Search state (ephemeral — resets on date change)
	let searchQuery = $state('');

	// Summary View state
	let daySummary = $state<DaySummary | null>(null);
	let summaryUpdatedAt = $state<string | null>(null);
	let summaryLoading = $state(false);

	// Persist selected card ID across webview navigations (e.g. external link → back)
	const SELECTED_CARD_KEY = 'laya_feed_selected_card';

	function formatDateLabel(dateStr: string): string {
		const today = localToday();
		const d = new Date();
		d.setDate(d.getDate() - 1);
		const yesterday = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
		if (dateStr === today) return 'Today';
		if (dateStr === yesterday) return 'Yesterday';
		return new Date(dateStr + 'T00:00:00').toLocaleDateString(undefined, {
			weekday: 'short',
			month: 'short',
			day: 'numeric'
		});
	}

	const isToday = $derived($feedDate === localToday());
	const filteredRecentCards = $derived(
		$feedFilters.spaceFilter
			? $recentCards.filter((e) => (e.space_id || 'default') === $feedFilters.spaceFilter)
			: $recentCards
	);

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
				date: $feedDate,
				space_id: f.spaceFilter || undefined
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
		searchQuery = '';
		loadGroups();
	});

	// Load summary when date changes or summary view is toggled on
	$effect(() => {
		$feedDate;
		if ($feedViewMode === 'summary') loadSummary();
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
				session_id?: string;
				selected_action_id?: string;
			};

			// Check if the new status is excluded by active filters
			if (payload.status) {
				const activeStatuses = $feedFilters.statusFilters;
				const isArchived = payload.status === 'archived';
				const excludedByArchiveToggle = isArchived && !$feedFilters.showArchived;
				const excludedByStatusFilter = activeStatuses.length > 0 && !activeStatuses.includes(payload.status);

				if (excludedByArchiveToggle || excludedByStatusFilter) {
					const cardId = msg.card_id!;
					// Mark card as exiting to trigger fade-out transition
					exitingCardIds = new Set([...exitingCardIds, cardId]);
					if (selectedCard?.card_id === cardId) {
						selectedCard = null;
						sessionStorage.removeItem(SELECTED_CARD_KEY);
					}
					// Remove after transition completes
					setTimeout(() => {
						groups = groups
							.map((g) => ({ ...g, cards: g.cards.filter((c) => c.card_id !== cardId) }))
							.filter((g) => g.cards.length > 0);
						totalGroups = groups.length;
						exitingCardIds = new Set([...exitingCardIds].filter((id) => id !== cardId));
					}, EXIT_DURATION);
					return;
				}
			}

			for (const group of groups) {
				const card = group.cards.find((c) => c.card_id === msg.card_id);
				if (card) {
					if (payload.status) {
						// When a card transitions from agent_running to a real status,
						// the full card data (suggested_actions, intelligence, etc.) has
						// changed significantly — reload to get fresh data from the API.
						const wasAgent = card.status === 'agent_running';
						card.status = payload.status as ActionCard['status'];
						if (payload.header) card.header = payload.header;
						if (payload.summary) card.summary = payload.summary;
						if (payload.priority) card.priority = payload.priority as ActionCard['priority'];
						if (payload.persona) card.persona = payload.persona as ActionCard['persona'];
						if (payload.selected_action_id) card.selected_action_id = payload.selected_action_id;
						group.has_pending = group.cards.some((c) => c.status === 'pending' || c.status === 'ready' || c.status === 'requires_approval');
						if (wasAgent && payload.status !== 'agent_running') {
							scheduleReload();
						}
					}
					if (payload.has_workspace !== undefined) card.has_workspace = payload.has_workspace;
					if (selectedCard?.card_id === msg.card_id) {
						selectedCard = { ...selectedCard, ...card } as ActionCard;
					}
					groups = groups;
					break;
				}
			}
		}
	});

	// Cards exiting due to filter mismatch — tracked for fade-out transition
	let exitingCardIds = $state(new Set<string>());
	const EXIT_DURATION = 250; // ms — fast but visible

	// ID of card to scroll into view after next render (set by gotoCard)
	let _scrollToCardId = $state<string | null>(null);

	function gotoCard(card: ActionCard) {
		let cardDate: string | null = null;
		if (card.created_at) {
			// created_at is stored as UTC in the DB (e.g. "2026-03-13 20:00:00") without
			// a timezone indicator. Append 'Z' so Date parses it as UTC, then extract the
			// local date — matching the timezone-aware grouping used by feedDate.
			const raw = card.created_at.endsWith('Z') || card.created_at.includes('+') ? card.created_at : card.created_at.replace(' ', 'T') + 'Z';
			const d = new Date(raw);
			cardDate = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
		}
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
		_scrollToCardId = cardId;
		// Wait for DOM to settle (group may need to expand first via $effect)
		const attempt = (tries: number) => {
			requestAnimationFrame(() => {
				const el = document.querySelector(`[data-card-id="${cardId}"]`);
				if (el) {
					el.scrollIntoView({ behavior: 'smooth', block: 'center' });
					// Brief highlight flash
					el.classList.add('ring-2', 'ring-laya-orange/60');
					setTimeout(() => el.classList.remove('ring-2', 'ring-laya-orange/60'), 1500);
					_scrollToCardId = null;
				} else if (tries > 0) {
					// Group may still be expanding — retry next frame
					attempt(tries - 1);
				} else {
					_scrollToCardId = null;
				}
			});
		};
		attempt(5);
	}

	function selectCard(card: ActionCard) {
		selectedCard = card;
		sessionStorage.setItem(SELECTED_CARD_KEY, card.card_id);
		trackCardVisit(card);
		// Fetch full card from API to ensure suggested_actions and other fields
		// that may be missing from WS-patched data are present.
		engineApi.getCard(card.card_id).then((fresh) => {
			if (selectedCard?.card_id === card.card_id) {
				selectedCard = fresh as ActionCard;
			}
		}).catch(() => {});
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
		$feedViewMode = 'card';
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

	function handleRecentCardClick(entry: RecentCardEntry) {
		// Find the card in currently loaded groups
		for (const g of groups) {
			const found = g.cards.find((c) => c.card_id === entry.card_id);
			if (found) {
				selectCard(found);
				scrollToCard(entry.card_id);
				return;
			}
		}
		// Card not in current view — fetch it to find its date, then navigate
		engineApi.getCard(entry.card_id).then((card) => {
			gotoCard(card as ActionCard);
			selectCard(card as ActionCard);
		}).catch(() => {});
	}

	function formatRecentTime(epochMs: number): string {
		const diff = Date.now() - epochMs;
		const mins = Math.floor(diff / 60_000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hours = Math.floor(mins / 60);
		if (hours < 24) return `${hours}h ago`;
		const days = Math.floor(hours / 24);
		return `${days}d ago`;
	}

	const totalCards = $derived(groups.reduce((sum, g) => sum + g.card_count, 0));
	const requiresApprovalCount = $derived(
		groups.reduce((sum, g) => sum + g.cards.filter((c) => c.status === 'requires_approval').length, 0)
	);
	const agentRunningCount = $derived(
		groups.reduce((sum, g) => sum + g.cards.filter((c) => c.status === 'agent_running').length, 0)
	);
	const failedCount = $derived(
		groups.reduce((sum, g) => sum + g.cards.filter((c) => c.status === 'failed').length, 0)
	);

	// Search filtering
	function cardMatchesSearch(card: ActionCard, terms: string[]): boolean {
		const searchable = [
			card.header,
			card.summary,
			card.category,
			card.entity_id,
			card.source_ref,
			card.actor_name,
			card.actor_email,
			card.space_name,
			...(card.intelligence ?? []),
			card.staged_output?.content,
			...(card.suggested_actions?.map((a) => a.label) ?? [])
		]
			.filter(Boolean)
			.join(' ')
			.toLowerCase();
		return terms.every((term) => searchable.includes(term));
	}

	const searchTerms = $derived(
		searchQuery.trim() === '' ? [] : searchQuery.toLowerCase().split(/\s+/).filter(Boolean)
	);

	const filteredGroups = $derived.by(() => {
		if (searchTerms.length === 0) return groups;
		return groups
			.map((group) => {
				// Check group-level fields first
				const groupText = [group.entity_title, group.platform].join(' ').toLowerCase();
				if (searchTerms.every((t) => groupText.includes(t))) return group;
				// Filter individual cards
				const matching = group.cards.filter((c) => cardMatchesSearch(c, searchTerms));
				if (matching.length === 0) return null;
				return {
					...group,
					cards: matching,
					card_count: matching.length,
					has_pending: matching.some(
						(c) => c.status === 'pending' || c.status === 'ready' || c.status === 'requires_approval'
					)
				};
			})
			.filter((g): g is CardGroup => g !== null);
	});

	const filteredDaySummary = $derived.by((): DaySummary | null => {
		if (!daySummary || searchTerms.length === 0) return daySummary;
		function filterItems(items: import('$lib/api/types').SummaryItem[]) {
			return items.filter((item) => {
				const text = item.text.toLowerCase();
				return searchTerms.every((t) => text.includes(t));
			});
		}
		const filtered: DaySummary = {
			events_and_meetings: filterItems(daySummary.events_and_meetings),
			action_items: filterItems(daySummary.action_items),
			key_updates: filterItems(daySummary.key_updates)
		};
		// Return null if everything was filtered out
		if (
			filtered.events_and_meetings.length === 0 &&
			filtered.action_items.length === 0 &&
			filtered.key_updates.length === 0
		)
			return null;
		return filtered;
	});

	const filteredTotalCards = $derived(filteredGroups.reduce((sum, g) => sum + g.card_count, 0));

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
	const columns = $derived(toColumns(filteredGroups));

	// Sort sections: group by sort_key when a non-date sort is active
	const sections = $derived.by(() => {
		if ($feedFilters.sortBy === 'newest' || $feedFilters.sortBy === 'oldest') return null;
		const map = new Map<string, CardGroup[]>();
		for (const g of filteredGroups) {
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

<div class="flex h-full flex-col">
	<!-- Sticky summary bar spanning full width -->
	<div class="flex items-center gap-2 pb-3">
		<div class="flex items-center gap-1.5">
			<span class="text-xs text-surface-500">{totalGroups} {totalGroups === 1 ? 'group' : 'groups'}</span>
			<span class="text-[10px] text-surface-600">·</span>
			<span class="text-xs text-surface-500">{totalCards} cards</span>
			{#if searchTerms.length > 0 && filteredTotalCards !== totalCards}
				<span class="inline-flex items-center gap-1 rounded-full bg-laya-orange/10 px-2 py-0.5 text-[10px] font-medium text-laya-orange">
					<svg class="h-2.5 w-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
					{filteredGroups.length} shown
				</span>
			{/if}
			{#if agentRunningCount > 0}
				<span class="inline-flex items-center gap-1 rounded-full bg-laya-coral/10 px-2 py-0.5 text-[10px] font-medium text-laya-coral">
					<span class="h-1.5 w-1.5 rounded-full bg-laya-coral animate-pulse"></span>
					{agentRunningCount} running
				</span>
			{/if}
			{#if failedCount > 0}
				<span class="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-400">
					<span class="h-1.5 w-1.5 rounded-full bg-red-400"></span>
					{failedCount} failed
				</span>
			{/if}
			{#if requiresApprovalCount > 0}
				<span class="inline-flex items-center gap-1 rounded-full bg-violet-500/10 px-2 py-0.5 text-[10px] font-medium text-violet-400">
					<span class="h-1.5 w-1.5 rounded-full bg-violet-400"></span>
					{requiresApprovalCount} {requiresApprovalCount === 1 ? 'needs' : 'need'} approval
				</span>
			{/if}
		</div>
		<div class="flex-1"></div>
		<!-- Recent Cards toggle -->
		<button
			class="flex items-center justify-center rounded-lg border px-2 py-1 transition-colors
				{$recentDrawerOpen
					? 'border-laya-orange/40 bg-laya-orange/10 text-laya-orange'
					: 'border-surface-700 bg-surface-800/60 text-surface-500 hover:text-surface-200 hover:border-surface-600'}"
			onclick={() => ($recentDrawerOpen = !$recentDrawerOpen)}
			title="Recent Cards"
		>
			<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
			</svg>
		</button>
		<!-- Search -->
		<div class="relative">
			<svg class="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-surface-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
			</svg>
			<input
				type="text"
				bind:value={searchQuery}
				placeholder="Search"
				class="h-7 w-48 rounded-lg border border-surface-700 bg-surface-800/60 pl-7 pr-7 text-xs text-surface-200 placeholder-surface-500 outline-none transition-colors focus:border-laya-orange/50 focus:ring-1 focus:ring-laya-orange/25"
			/>
			{#if searchQuery}
				<button
					class="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-surface-500 hover:text-surface-300"
					onclick={() => (searchQuery = '')}
					title="Clear search"
				>
					<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			{/if}
		</div>
		<!-- View toggle -->
		<div class="flex items-center rounded-lg border border-surface-700 bg-surface-800/60 p-0.5">
			<button
				class="flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors {$feedViewMode === 'card' ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-400 hover:text-surface-200'}"
				onclick={() => ($feedViewMode = 'card')}
				title="Card View"
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
				</svg>
			</button>
			<button
				class="flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors {$feedViewMode === 'list' ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-400 hover:text-surface-200'}"
				onclick={() => ($feedViewMode = 'list')}
				title="List View"
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
				</svg>
			</button>
			<button
				class="flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors {$feedViewMode === 'summary' ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-400 hover:text-surface-200'}"
				onclick={() => ($feedViewMode = 'summary')}
				title="Summary View"
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
				</svg>
			</button>
		</div>
	</div>

	<!-- Content area: recent drawer + cards + detail panel side by side -->
	<div class="flex min-h-0 flex-1 gap-4">
		<!-- Recent Cards drawer -->
		{#if $recentDrawerOpen}
			<div class="flex w-[260px] flex-shrink-0 flex-col overflow-hidden rounded-xl border border-surface-700/50 bg-surface-900/60">
				<div class="flex items-center justify-between border-b border-surface-700/50 px-3 py-2">
					<span class="text-xs font-medium text-surface-300">Recent Cards</span>
					<div class="flex items-center gap-1">
						{#if filteredRecentCards.length > 0}
							<button
								class="rounded p-0.5 text-surface-600 transition-colors hover:text-surface-300"
								onclick={() => clearRecentCards()}
								title="Clear history"
							>
								<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
								</svg>
							</button>
						{/if}
						<button
							class="rounded p-0.5 text-surface-600 transition-colors hover:text-surface-300"
							onclick={() => ($recentDrawerOpen = false)}
							title="Close"
						>
							<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
							</svg>
						</button>
					</div>
				</div>
				<div class="flex-1 overflow-y-auto">
					{#if filteredRecentCards.length === 0}
						<div class="flex flex-col items-center justify-center px-4 py-8 text-surface-600">
							<svg class="mb-2 h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
							</svg>
							<p class="text-[11px]">{$feedFilters.spaceFilter ? 'No recent cards in this space' : 'No recent cards yet'}</p>
							<p class="mt-0.5 text-[10px] text-surface-700">Cards you view will appear here</p>
						</div>
					{:else}
						{#each filteredRecentCards as entry (entry.card_id)}
							<button
								class="flex w-full flex-col gap-0.5 border-b border-surface-800/50 px-3 py-2 text-left transition-colors hover:bg-surface-800/60
									{selectedCard?.card_id === entry.card_id ? 'bg-laya-orange/5 border-l-2 border-l-laya-orange/40' : ''}"
								onclick={() => handleRecentCardClick(entry)}
							>
								<div class="flex items-start justify-between gap-2">
									<span class="line-clamp-1 text-xs text-surface-200">{entry.header}</span>
									<span class="shrink-0 text-[9px] text-surface-600">{formatRecentTime(entry.visited_at)}</span>
								</div>
								<span class="line-clamp-1 text-[10px] text-surface-500">
									{#if entry.source_ref}{entry.source_ref}{:else if entry.entity_id}{entry.entity_id}{:else if entry.category}{entry.category}{/if}
									{#if entry.space_name}
										<span class="text-surface-600"> · {entry.space_name}</span>
									{/if}
								</span>
							</button>
						{/each}
					{/if}
				</div>
			</div>
		{/if}
		<!-- Cards / Summary / List section -->
		<div bind:this={containerEl} class="flex min-w-0 flex-1 flex-col overflow-y-auto p-3">
			<!-- Summary View -->
			{#if $feedViewMode === 'summary'}
				<div class="flex-1 overflow-y-auto rounded-xl border border-surface-700/50 bg-surface-900/30 p-6">
					{#if summaryLoading}
						<div class="flex h-full items-center justify-center text-surface-400">
							<span class="text-sm">Loading summary...</span>
						</div>
					{:else if !filteredDaySummary && searchTerms.length > 0 && daySummary}
						<div class="flex h-full flex-col items-center justify-center text-surface-500">
							<svg class="mb-2 h-8 w-8 text-surface-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
							</svg>
							<p class="text-sm">No summary items match "<span class="text-surface-300">{searchQuery}</span>"</p>
							<button class="mt-2 text-xs text-laya-orange hover:underline" onclick={() => (searchQuery = '')}>Clear search</button>
						</div>
					{:else}
						<DaySummaryComponent summary={filteredDaySummary} updatedAt={summaryUpdatedAt} ongotocard={handleSummaryGotoCard} spaceFilter={$feedFilters.spaceFilter} />
					{/if}
				</div>
			<!-- List / Card shared empty states -->
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
			{:else if filteredGroups.length === 0 && searchTerms.length > 0}
				<div class="py-12 text-center text-surface-500">
					<svg class="mx-auto mb-2 h-8 w-8 text-surface-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
					</svg>
					<p class="text-sm">No cards match "<span class="text-surface-300">{searchQuery}</span>"</p>
					<button class="mt-2 text-xs text-laya-orange hover:underline" onclick={() => (searchQuery = '')}>Clear search</button>
				</div>
			<!-- ── LIST VIEW ── -->
			{:else if $feedViewMode === 'list'}
				{#if sections}
					<!-- Sorted list view with section separators -->
					{#each sections as [sectionTitle, sectionGroups], si}
						{@const isCollapsed = collapsedSections.has(sectionTitle)}
						<div
							class="flex cursor-pointer items-center gap-3 pr-3 {si > 0 ? 'mt-5' : ''} mb-2 select-none"
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
							<div class="flex flex-col gap-1 mb-2">
								{#each sectionGroups as group (group.entity_id)}
									{#if group.card_count === 1}
										<ListRow card={group.cards[0]} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} />
									{:else}
										<ListGroupComponent {group} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} scrollToCardId={_scrollToCardId} />
									{/if}
								{/each}
							</div>
						{/if}
					{/each}
				{:else}
					<!-- Default list view -->
					<div class="flex flex-col gap-1">
						{#each filteredGroups as group (group.entity_id)}
							{#if group.card_count === 1}
								<ListRow card={group.cards[0]} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} />
							{:else}
								<ListGroupComponent {group} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} scrollToCardId={_scrollToCardId} />
							{/if}
						{/each}
					</div>
				{/if}
			<!-- ── CARD VIEW ── -->
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
									{@const isGroupExiting = group.cards.every((c) => exitingCardIds.has(c.card_id))}
									<div class="card-exit-wrap {isGroupExiting ? 'card-exiting' : ''}">
										{#if group.card_count === 1}
											<ActionCardComponent card={group.cards[0]} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} hasSelection={!!selectedCard} />
										{:else}
											<CardGroupComponent {group} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} hasSelection={!!selectedCard} scrollToCardId={_scrollToCardId} />
										{/if}
									</div>
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
									<CardGroupComponent {group} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} hasSelection={!!selectedCard} scrollToCardId={_scrollToCardId} />
								{/if}
							{/each}
						</div>
					{/each}
				</div>
			{/if}
		</div>

		<!-- Detail panel (hidden in summary view) -->
		{#if $feedViewMode !== 'summary'}
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
</div>
