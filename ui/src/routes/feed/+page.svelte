<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import { feedFilters, feedDate, feedPrevDate, feedNextDate, localToday } from '$lib/stores/feedFilters';
	import type { ActionCard, CardGroup, DaySummary } from '$lib/api/types';
	import CardGroupComponent from '$lib/components/feed/CardGroup.svelte';
	import ActionCardComponent from '$lib/components/feed/ActionCard.svelte';
	import CardDetail from '$lib/components/feed/CardDetail.svelte';
	import DaySummaryComponent from '$lib/components/feed/DaySummary.svelte';
	import { feedViewMode } from '$lib/stores/feedView';
	import { feedSelection } from '$lib/stores/feedSelection';
	import ListRow from '$lib/components/feed/ListRow.svelte';
	import ListGroupComponent from '$lib/components/feed/ListGroup.svelte';
	import BulkActionsDropdown from '$lib/components/feed/BulkActionsDropdown.svelte';
	import { recentCards, recentDrawerOpen, trackCardVisit, clearRecentCards, type RecentCardEntry } from '$lib/stores/recentCards';
	import { pendingCardId } from '$lib/stores/chat';
	import { spaces } from '$lib/stores/spaces';

	// Filter toolbar state
	let filterPopoverOpen = $state(false);
	const activeStatusCount = $derived($feedFilters.statusFilters.length);
	const activePriorityCount = $derived($feedFilters.priorityFilters.length);
	const activeSpaceCount = $derived($feedFilters.spaceFilter.length);
	const hasActiveFilters = $derived(activeStatusCount > 0 || activePriorityCount > 0 || $feedFilters.showArchived || $feedFilters.showBookmarked || activeSpaceCount > 0);

	function toggleFilter(arr: string[], value: string): string[] {
		return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value];
	}

	function closeFilterDropdown(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (!target.isConnected) return;
		if (!target.closest('.filter-dropdown')) {
			filterPopoverOpen = false;
		}
	}

	$effect(() => {
		document.addEventListener('click', closeFilterDropdown);
		return () => document.removeEventListener('click', closeFilterDropdown);
	});

	let groups = $state<CardGroup[]>([]);
	let totalGroups = $state(0);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let selectedCard = $state<ActionCard | null>(null);
	let detailPanelOpen = $state(false);
	let showIntegrationsPopup = $state(false);
	// Tracks the last card shown in the detail panel — survives dismiss so close can scroll back
	let lastDetailCardId = $state<string | null>(null);

	// Track last non-summary view so we can restore it when clicking summary items
	let lastNonSummaryView = $state<'card' | 'list'>(
		($feedViewMode === 'card' || $feedViewMode === 'list') ? $feedViewMode : 'card'
	);

	// Update lastNonSummaryView whenever the user switches to card or list
	$effect(() => {
		if ($feedViewMode === 'card' || $feedViewMode === 'list') {
			lastNonSummaryView = $feedViewMode;
		}
	});

	// When switching views, scroll selected card into view.
	// When coming FROM summary view, suppress FLIP since cards are rendering fresh.
	let _prevViewMode = $state($feedViewMode);
	$effect(() => {
		const mode = $feedViewMode;
		const fromSummary = _prevViewMode === 'summary';
		_prevViewMode = mode;

		if (mode === 'card' || mode === 'list') {
			if (fromSummary) {
				// Cards are rendering fresh — no old positions to FLIP from.
				// Suppress ResizeObserver FLIP during initial layout.
				panelTransitioning = true;
				setTimeout(() => { panelTransitioning = false; }, 350);
			}
			if (selectedCard) {
				tick().then(() => scrollToCard(selectedCard!.card_id));
			}
		}
	});

	// Auto-open detail panel when a card is selected (skip FLIP — scroll will follow)
	$effect(() => {
		if (selectedCard) openDetailPanel(true);
	});

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
		$feedFilters.spaceFilter.length > 0
			? $recentCards.filter((e) => $feedFilters.spaceFilter.includes(e.space_id || 'default'))
			: $recentCards
	);

	let _fetchId = 0;
	let _reloadTimer: ReturnType<typeof setTimeout> | null = null;

	// Promise that resolves when any in-progress FLIP animation finishes
	let _flipSettled: Promise<void> = Promise.resolve();

	// Capture card positions for FLIP animation before data changes
	function captureCardPositions(): Map<string, DOMRect> {
		const positions = new Map<string, DOMRect>();
		if (!containerEl) return positions;
		containerEl.querySelectorAll('[data-entity-id]').forEach((el) => {
			positions.set((el as HTMLElement).dataset.entityId!, el.getBoundingClientRect());
		});
		return positions;
	}

	// Apply FLIP animation from old positions to current positions
	// When instant=true, skip animations (used for panel open/close to avoid jarring double transitions)
	async function animateFlip(oldPositions: Map<string, DOMRect>, instant = false) {
		if (!containerEl || oldPositions.size === 0) return;
		if (instant) {
			_flipSettled = Promise.resolve();
			return;
		}
		// Signal that a FLIP animation is in progress; resolves after animations finish
		_flipSettled = new Promise((resolve) => setTimeout(resolve, 350));
		await tick();
		containerEl.querySelectorAll('[data-entity-id]').forEach((el) => {
			const htmlEl = el as HTMLElement;
			const id = htmlEl.dataset.entityId!;
			const oldRect = oldPositions.get(id);
			if (!oldRect) {
				// New card — animate entrance
				htmlEl.style.opacity = '0';
				htmlEl.style.transform = 'scale(0.95)';
				htmlEl.style.transition = 'none';
				requestAnimationFrame(() => {
					htmlEl.style.transition = 'opacity 250ms ease, transform 250ms ease';
					htmlEl.style.opacity = '';
					htmlEl.style.transform = '';
					htmlEl.addEventListener('transitionend', () => {
						htmlEl.style.transition = '';
					}, { once: true });
				});
				return;
			}

			const newRect = el.getBoundingClientRect();
			const dx = oldRect.left - newRect.left;
			const dy = oldRect.top - newRect.top;
			if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;

			htmlEl.style.transform = `translate(${dx}px, ${dy}px)`;
			htmlEl.style.transition = 'none';
			requestAnimationFrame(() => {
				htmlEl.style.transition = 'transform 300ms ease';
				htmlEl.style.transform = '';
				htmlEl.addEventListener('transitionend', () => {
					htmlEl.style.transition = '';
				}, { once: true });
			});
		});
	}

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
				sort_asc: f.sortAsc || undefined,
				show_archived: f.showArchived || undefined,
				date: f.showBookmarked ? undefined : $feedDate,
				space_id: f.spaceFilter.length ? f.spaceFilter.join(',') : undefined,
				bookmarked: f.showBookmarked || undefined
			});
			if (id !== _fetchId) return;

			// Capture positions before updating groups for FLIP animation
			const oldPositions = captureCardPositions();

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

			// FLIP animate existing cards + fade in new ones
			animateFlip(oldPositions);
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

	// One-time integrations setup popup — show once on first feed visit after setup
	$effect(() => {
		if (!loading && groups !== undefined) {
			const dismissed = localStorage.getItem('laya-integrations-popup-dismissed');
			if (!dismissed) {
				showIntegrationsPopup = true;
				localStorage.setItem('laya-integrations-popup-dismissed', '1');
			}
		}
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

		if (!['card_created', 'card_deleted', 'card_updated', 'group_carried_forward'].includes(msg.type)) return;
		_lastProcessedMsg = msg;

		if (msg.type === 'card_created' || msg.type === 'group_carried_forward') {
			// Only auto-reload if viewing today (new cards / carried-forward groups land on today)
			if (isToday) scheduleReload();
		} else if (msg.type === 'card_deleted' && msg.card_id) {
			if (selectedCard?.card_id === msg.card_id) {
				selectedCard = null;
				sessionStorage.removeItem(SELECTED_CARD_KEY);
			}
			feedSelection.removeDeleted(msg.card_id);
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
					feedSelection.removeDeleted(cardId);
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

			let found = false;
			for (const group of groups) {
				const card = group.cards.find((c) => c.card_id === msg.card_id);
				if (card) {
					found = true;
					if (payload.status) {
						// When a card transitions from agent_running to a real status,
						// the full card data (suggested_actions, intelligence, etc.) has
						// changed significantly — reload to get fresh data from the API.
						const wasAgent = card.status === 'agent_running';
						card.status = payload.status as ActionCard['status'];
						if (payload.header) card.header = payload.header;
						if (payload.summary) card.summary = payload.summary;
						if (payload.selected_action_id) card.selected_action_id = payload.selected_action_id;
						group.has_pending = group.cards.some((c) => c.status === 'pending' || c.status === 'ready' || c.status === 'requires_approval');
						if (wasAgent && payload.status !== 'agent_running') {
							scheduleReload();
						}
					}
					if (payload.priority) card.priority = payload.priority as ActionCard['priority'];
					if (payload.persona) card.persona = payload.persona as ActionCard['persona'];
					if (payload.has_workspace !== undefined) card.has_workspace = payload.has_workspace;
					if (selectedCard?.card_id === msg.card_id) {
						selectedCard = { ...selectedCard, ...card } as ActionCard;
					}
					groups = groups;
					break;
				}
			}
			// Card not in current groups — may have been created while on another
			// page or the card_created message was missed. Reload to pick it up.
			if (!found) scheduleReload();
		}
	});

	// Cards exiting due to filter mismatch — tracked for fade-out transition
	let exitingCardIds = $state(new Set<string>());
	const EXIT_DURATION = 250; // ms — fast but visible

	// ID of card to scroll into view after next render (set by gotoCard)
	let _scrollToCardId = $state<string | null>(null);

	function gotoCard(card: ActionCard) {
		let cardDate: string | null = null;
		// Use group_active_at (carry-forward date) if available, otherwise fall back to created_at.
		// group_active_at reflects when the card's entity group was last active, which is the
		// date the card will actually appear on in the feed due to group carry-forward.
		const dateSource = card.group_active_at || card.created_at;
		if (dateSource) {
			const raw = dateSource.endsWith('Z') || dateSource.includes('+') ? dateSource : dateSource.replace(' ', 'T') + 'Z';
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
		// Wait for any in-progress FLIP animation to finish, then scroll
		_flipSettled.then(() => {
			const attempt = (tries: number) => {
				requestAnimationFrame(() => {
					const el = document.querySelector(`[data-card-id="${cardId}"]`);
					if (el) {
						el.scrollIntoView({ behavior: 'smooth', block: 'center' });
						// Brief highlight flash
						el.classList.add('card-highlight-fade');
						el.addEventListener('animationend', () => el.classList.remove('card-highlight-fade'), { once: true });
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
		});
	}

	// Handle card link clicks from chat — find the card and navigate to it
	$effect(() => {
		const rawId = $pendingCardId;
		if (!rawId) return;
		pendingCardId.set(null);
		// Normalize: LLM may omit the "card_" prefix
		const cardId = rawId.startsWith('card_') ? rawId : `card_${rawId}`;
		// Look for the card in currently loaded groups first
		for (const g of groups) {
			const found = g.cards.find((c) => c.card_id === cardId);
			if (found) {
				gotoCard(found);
				selectCard(found);
				return;
			}
		}
		// Card not in current view — fetch it from API and navigate to its date
		engineApi.getCard(cardId).then((card) => {
			gotoCard(card as ActionCard);
			selectCard(card as ActionCard);
		}).catch(() => {
			// Card not found — scroll attempt with just the ID
			scrollToCard(cardId);
		});
	});

	function selectCard(card: ActionCard) {
		selectedCard = card;
		lastDetailCardId = card.card_id;
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
		detailPanelOpen = false;
		lastDetailCardId = null;
		sessionStorage.removeItem(SELECTED_CARD_KEY);
	}

	// Dismiss the active card without closing the panel (X button in CardDetail)
	function dismissActiveCard() {
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
		// Restore the last non-summary view (card or list)
		$feedViewMode = lastNonSummaryView;
		// Find the card in groups
		for (const g of groups) {
			const found = g.cards.find((c) => c.card_id === cardId);
			if (found) {
				selectCard(found);
				// scrollToCard is also triggered by the $effect on feedViewMode change,
				// but call it explicitly to ensure it fires after selectCard
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
	const DETAIL_PANEL_WIDTH = 420;

	let containerEl = $state<HTMLElement | null>(null);
	let numColumns = $state(3);
	let panelTransitioning = $state(false);

	// FLIP helper: capture positions, update columns, animate cards to new positions
	// When instant=true, repack without animation (used for panel open/close)
	async function flipColumns(newCols: number, instant = false) {
		if (newCols === numColumns || !containerEl) return;
		const oldPositions = captureCardPositions();
		numColumns = newCols;
		animateFlip(oldPositions, instant);
	}

	$effect(() => {
		if (!containerEl) return;
		const observer = new ResizeObserver(([entry]) => {
			// Skip intermediate reflows while detail panel is sliding
			if (panelTransitioning) return;

			const w = entry.contentRect.width;
			const newCols = Math.max(1, Math.floor((w + COL_GAP) / (CARD_WIDTH + COL_GAP)));
			flipColumns(newCols);
		});
		observer.observe(containerEl);
		return () => observer.disconnect();
	});

	// Pre-calculate final column count when detail panel opens/closes
	// so cards reflow once to the final layout before the panel animates
	// skipFlip=true when opening because a card was clicked (scroll will follow, so skip jarring FLIP)
	// skipFlip=false (default) when user manually opens via chevron (normal FLIP animation)
	function openDetailPanel(skipFlip = false) {
		if (detailPanelOpen || !containerEl) {
			detailPanelOpen = true;
			return;
		}
		// Panel will take DETAIL_PANEL_WIDTH + COL_GAP from the container
		const currentWidth = containerEl.clientWidth;
		const finalWidth = currentWidth - DETAIL_PANEL_WIDTH - COL_GAP;
		const finalCols = Math.max(1, Math.floor((finalWidth + COL_GAP) / (CARD_WIDTH + COL_GAP)));

		panelTransitioning = true;
		flipColumns(finalCols, skipFlip);
		detailPanelOpen = true;

		// Scroll the selected card into view after the panel slide finishes
		if (skipFlip && selectedCard) {
			setTimeout(() => scrollToCard(selectedCard!.card_id), 320);
		}

		// Allow ResizeObserver to resume after panel transition ends (300ms duration)
		setTimeout(() => { panelTransitioning = false; }, 350);
	}

	// Scroll to a card and show a fading highlight border (used when panel closes)
	function scrollToCardWithFade(cardId: string) {
		_flipSettled.then(() => {
			const attempt = (tries: number) => {
				requestAnimationFrame(() => {
					const el = document.querySelector(`[data-card-id="${cardId}"]`);
					if (el) {
						el.scrollIntoView({ behavior: 'smooth', block: 'center' });
						// Fading orange ring highlight via CSS animation
						el.classList.add('card-highlight-fade');
						el.addEventListener('animationend', () => {
							el.classList.remove('card-highlight-fade');
						}, { once: true });
					} else if (tries > 0) {
						attempt(tries - 1);
					}
				});
			};
			attempt(5);
		});
	}

	function closeDetailPanel() {
		if (!detailPanelOpen || !containerEl) {
			closeDetail();
			return;
		}
		// Use lastDetailCardId which survives dismiss (X button clears selectedCard but not this)
		const lastCardId = lastDetailCardId;

		// Panel will free DETAIL_PANEL_WIDTH + COL_GAP back to the container
		const currentWidth = containerEl.clientWidth;
		const finalWidth = currentWidth + DETAIL_PANEL_WIDTH + COL_GAP;
		const finalCols = Math.max(1, Math.floor((finalWidth + COL_GAP) / (CARD_WIDTH + COL_GAP)));

		panelTransitioning = true;
		// Skip FLIP when scroll-back will also run (avoids jarring double transition)
		flipColumns(finalCols, !!lastCardId);
		closeDetail();

		// After panel slide completes, scroll to the card that was selected and show fading highlight
		if (lastCardId) {
			setTimeout(() => scrollToCardWithFade(lastCardId), 350);
		}

		setTimeout(() => { panelTransitioning = false; }, 350);
	}

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

	// ── Bulk selection ──────────────────────────────────────────────────
	const allVisibleCardIds = $derived(filteredGroups.flatMap(g => g.cards.map(c => c.card_id)));
	const allVisibleCardsMap = $derived(
		new Map(filteredGroups.flatMap(g => g.cards.map(c => [c.card_id, c] as const)))
	);
	const selectionCount = $derived($feedSelection.size);
	const hasSelection = $derived(selectionCount > 0);
	const allVisibleSelected = $derived(allVisibleCardIds.length > 0 && allVisibleCardIds.every(id => $feedSelection.has(id)));
	const selectedCards = $derived(
		[...$feedSelection].map(id => allVisibleCardsMap.get(id)).filter(Boolean) as ActionCard[]
	);

	// Clear selection when leaving list view
	$effect(() => {
		if ($feedViewMode !== 'list') feedSelection.deselectAll();
	});

	function handleBulkToggle(cardId: string, event: MouseEvent) {
		if (event.shiftKey && feedSelection.getLastClicked()) {
			const lastId = feedSelection.getLastClicked()!;
			const startIdx = allVisibleCardIds.indexOf(lastId);
			const endIdx = allVisibleCardIds.indexOf(cardId);
			if (startIdx !== -1 && endIdx !== -1) {
				const [from, to] = [Math.min(startIdx, endIdx), Math.max(startIdx, endIdx)];
				feedSelection.selectMany(allVisibleCardIds.slice(from, to + 1));
			}
		} else {
			feedSelection.toggleCard(cardId);
		}
		feedSelection.setLastClicked(cardId);
	}

	function handleBulkToggleGroup(cardIds: string[], selected: boolean) {
		if (selected) feedSelection.selectMany(cardIds);
		else feedSelection.deselectMany(cardIds);
	}
</script>

<div class="flex h-full flex-col">
	<!-- Feed toolbar -->
	<div class="flex items-center gap-1.5 pb-2">
		<!-- Stats -->
		{#if hasSelection && $feedViewMode === 'list'}
			<div class="flex items-center gap-2">
				<span class="text-xs font-medium text-laya-orange">{selectionCount} selected</span>
				<span class="text-[10px] text-surface-600">·</span>
				{#if !allVisibleSelected}
					<button
						class="text-xs text-surface-400 hover:text-surface-200 transition-colors"
						onclick={() => feedSelection.selectMany(allVisibleCardIds)}
					>
						Select All
					</button>
					<span class="text-[10px] text-surface-600">·</span>
				{/if}
				<button
					class="text-xs text-surface-400 hover:text-surface-200 transition-colors"
					onclick={() => feedSelection.deselectAll()}
				>
					Deselect All
				</button>
				<div class="ml-1">
					<BulkActionsDropdown {selectedCards} ondelete={handleDelete} />
				</div>
			</div>
		{:else}
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
		{/if}

		<div class="flex-1"></div>

		<!-- Filter popover -->
		<div class="filter-dropdown relative">
			<button
				onclick={() => (filterPopoverOpen = !filterPopoverOpen)}
				class="relative flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition-colors
					{hasActiveFilters
						? 'border-laya-orange/30 bg-laya-orange/10 text-laya-orange'
						: 'border-surface-700 bg-surface-800/60 text-surface-400 hover:text-surface-200 hover:border-surface-600'}"
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
				</svg>
				Filters
				{#if hasActiveFilters}
					<span class="flex h-4 w-4 items-center justify-center rounded-full bg-laya-orange text-[9px] font-bold text-surface-900">{activeStatusCount + activePriorityCount + activeSpaceCount + ($feedFilters.showArchived ? 1 : 0) + ($feedFilters.showBookmarked ? 1 : 0)}</span>
				{/if}
			</button>

			{#if filterPopoverOpen}
				<div class="absolute left-0 top-full z-50 mt-1.5 w-64 rounded-xl border border-surface-600 bg-surface-800 p-3 shadow-xl shadow-black/30">
					<!-- Sort -->
					<div class="mb-3">
						<div class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Sort</div>
						<div class="flex items-center gap-1.5">
							<div class="flex flex-1 items-center gap-1.5 rounded-lg border border-surface-700 bg-surface-900/60 px-2 py-1">
								<svg class="h-3.5 w-3.5 text-surface-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4h13M3 8h9m-9 4h6m4 0l4-4m0 0l4 4m-4-4v12" />
								</svg>
								<select
									bind:value={$feedFilters.sortBy}
									class="flex-1 bg-transparent text-xs text-surface-200 outline-none cursor-pointer appearance-none pr-4"
									style="background-image: url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%2712%27 height=%2712%27 viewBox=%270 0 24 24%27 fill=%27none%27 stroke=%27%23888%27 stroke-width=%272%27%3E%3Cpath d=%27M6 9l6 6 6-6%27/%3E%3C/svg%3E'); background-repeat: no-repeat; background-position: right 0 center;"
								>
									<option value="newest">Newest</option>
									<option value="priority">Priority</option>
									<option value="status">Status</option>
									<option value="persona">Persona</option>
									<option value="category">Category</option>
									<option value="platform">Source</option>
								</select>
							</div>
							<button
								aria-label="Toggle sort direction"
								class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-surface-700 bg-surface-900/60 text-surface-400 transition-colors hover:bg-surface-700 hover:text-surface-200"
								onclick={() => ($feedFilters.sortAsc = !$feedFilters.sortAsc)}
							>
								<svg class="h-3 w-3 transition-transform {$feedFilters.sortAsc ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 14l-7 7m0 0l-7-7m7 7V3" />
								</svg>
							</button>
						</div>
					</div>

					<!-- Workspace -->
					{#if $spaces.length > 1}
						<div class="mb-3">
							<div class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Workspace</div>
							<div class="space-y-0.5">
								{#each $spaces as space}
									<button
										class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors hover:bg-surface-700
											{$feedFilters.spaceFilter.includes(space.space_id) ? 'text-laya-orange' : 'text-surface-300'}"
										onclick={() => ($feedFilters.spaceFilter = toggleFilter($feedFilters.spaceFilter, space.space_id))}
									>
										<span class="flex h-4 w-4 items-center justify-center rounded border {$feedFilters.spaceFilter.includes(space.space_id) ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-600'}">
											{#if $feedFilters.spaceFilter.includes(space.space_id)}
												<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
													<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
												</svg>
											{/if}
										</span>
										<span class="h-2 w-2 rounded-full shrink-0" style="background-color: {space.color}"></span>
										{space.name}
									</button>
								{/each}
							</div>
						</div>
					{/if}

					<!-- Status -->
					<div class="mb-3">
						<div class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Status</div>
						<div class="space-y-0.5">
							{#each [['pending', 'Processing'], ['ready', 'Ready'], ['requires_approval', 'Needs Approval'], ['agent_running', 'Running'], ['failed', 'Failed'], ['done', 'Done'], ['dismissed', 'Dismissed']] as [value, label]}
								<button
									class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors hover:bg-surface-700
										{$feedFilters.statusFilters.includes(value) ? 'text-laya-orange' : 'text-surface-300'}"
									onclick={() => ($feedFilters.statusFilters = toggleFilter($feedFilters.statusFilters, value))}
								>
									<span class="flex h-4 w-4 items-center justify-center rounded border {$feedFilters.statusFilters.includes(value) ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-600'}">
										{#if $feedFilters.statusFilters.includes(value)}
											<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
											</svg>
										{/if}
									</span>
									{label}
								</button>
							{/each}
						</div>
					</div>

					<!-- Priority -->
					<div class="mb-3">
						<div class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Priority</div>
						<div class="space-y-0.5">
							{#each [['CRITICAL', 'Critical'], ['HIGH', 'High'], ['MEDIUM', 'Medium'], ['LOW', 'Low']] as [value, label]}
								<button
									class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors hover:bg-surface-700
										{$feedFilters.priorityFilters.includes(value) ? 'text-laya-orange' : 'text-surface-300'}"
									onclick={() => ($feedFilters.priorityFilters = toggleFilter($feedFilters.priorityFilters, value))}
								>
									<span class="flex h-4 w-4 items-center justify-center rounded border {$feedFilters.priorityFilters.includes(value) ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-600'}">
										{#if $feedFilters.priorityFilters.includes(value)}
											<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
											</svg>
										{/if}
									</span>
									{label}
								</button>
							{/each}
						</div>
					</div>

					<!-- Toggles -->
					<div class="mb-2 space-y-0.5">
						<button
							class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors hover:bg-surface-700
								{$feedFilters.showArchived ? 'text-laya-orange' : 'text-surface-300'}"
							onclick={() => ($feedFilters.showArchived = !$feedFilters.showArchived)}
						>
							<span class="flex h-4 w-4 items-center justify-center rounded border {$feedFilters.showArchived ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-600'}">
								{#if $feedFilters.showArchived}
									<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
									</svg>
								{/if}
							</span>
							Show Archived
						</button>
					</div>

					<!-- Clear all -->
					{#if hasActiveFilters}
						<div class="border-t border-surface-700 pt-2">
							<button
								class="w-full rounded-md px-2 py-1 text-[11px] font-medium text-surface-500 transition-colors hover:text-surface-300 hover:bg-surface-700"
								onclick={() => {
									$feedFilters.statusFilters = [];
									$feedFilters.priorityFilters = [];
									$feedFilters.showArchived = false;
									$feedFilters.showBookmarked = false;
									$feedFilters.spaceFilter = [];
								}}
							>
								Clear all filters
							</button>
						</div>
					{/if}
				</div>
			{/if}
		</div>

		<!-- Bookmarks toggle -->
		<button
			onclick={() => ($feedFilters.showBookmarked = !$feedFilters.showBookmarked)}
			class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition-colors
				{$feedFilters.showBookmarked
					? 'border-laya-orange/30 bg-laya-orange/10 text-laya-orange'
					: 'border-surface-700 bg-surface-800/60 text-surface-400 hover:text-surface-200 hover:border-surface-600'}"
		>
			<svg class="h-3.5 w-3.5" fill={$feedFilters.showBookmarked ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
			</svg>
			Bookmarks
		</button>

		<!-- Separator -->
		<div class="h-5 w-px bg-surface-700/60 mx-1"></div>

		<!-- Recent Cards toggle -->
		<div class="group/tip relative">
			<button
				class="flex items-center justify-center rounded-lg border px-2 py-1 transition-colors
					{$recentDrawerOpen
						? 'border-laya-orange/40 bg-laya-orange/10 text-laya-orange'
						: 'border-surface-700 bg-surface-800/60 text-surface-500 hover:text-surface-200 hover:border-surface-600'}"
				onclick={() => ($recentDrawerOpen = !$recentDrawerOpen)}
				aria-label="Recent Cards"
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
				</svg>
			</button>
			<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/tip:opacity-100">Recent Cards</span>
		</div>
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
			<div class="group/tip relative">
				<button
					class="flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors {$feedViewMode === 'card' ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-400 hover:text-surface-200'}"
					onclick={() => ($feedViewMode = 'card')}
					aria-label="Card View"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
					</svg>
				</button>
				<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/tip:opacity-100">Card View</span>
			</div>
			<div class="group/tip relative">
				<button
					class="flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors {$feedViewMode === 'list' ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-400 hover:text-surface-200'}"
					onclick={() => ($feedViewMode = 'list')}
					aria-label="List View"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
					</svg>
				</button>
				<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/tip:opacity-100">List View</span>
			</div>
			<div class="group/tip relative">
				<button
					class="flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors {$feedViewMode === 'summary' ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-400 hover:text-surface-200'}"
					onclick={() => ($feedViewMode = 'summary')}
					aria-label="Summary View"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
					</svg>
				</button>
				<span class="pointer-events-none absolute right-0 top-full z-50 mt-1.5 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/tip:opacity-100">Summary View</span>
			</div>
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
							<p class="text-[11px]">{$feedFilters.spaceFilter.length ? 'No recent cards in selected spaces' : 'No recent cards yet'}</p>
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
					<p class="text-lg">{$feedFilters.showBookmarked ? 'No bookmarked cards' : `No cards for ${formatDateLabel($feedDate)}`}</p>
					<p class="mt-1 text-sm">
						{#if $feedFilters.showBookmarked}
							Bookmark cards to save them for later
						{:else if $feedPrevDate}
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
										<ListRow card={group.cards[0]} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} bulkSelected={$feedSelection.has(group.cards[0].card_id)} onbulktoggle={handleBulkToggle} hasSelection={!!selectedCard} />
									{:else}
										<ListGroupComponent {group} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} scrollToCardId={_scrollToCardId} bulkSelectedIds={$feedSelection} onbulktoggle={handleBulkToggle} onbulktogglegroup={handleBulkToggleGroup} hasSelection={!!selectedCard} />
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
								<ListRow card={group.cards[0]} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} bulkSelected={$feedSelection.has(group.cards[0].card_id)} onbulktoggle={handleBulkToggle} hasSelection={!!selectedCard} />
							{:else}
								<ListGroupComponent {group} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} scrollToCardId={_scrollToCardId} bulkSelectedIds={$feedSelection} onbulktoggle={handleBulkToggle} onbulktogglegroup={handleBulkToggleGroup} hasSelection={!!selectedCard} />
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
									<div data-entity-id={group.entity_id} class="card-exit-wrap {isGroupExiting ? 'card-exiting' : ''}">
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
								<div data-entity-id={group.entity_id}>
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
		</div>

		<!-- Detail panel (hidden in summary view) -->
		{#if $feedViewMode !== 'summary'}
			<div class="relative flex flex-shrink-0">
				<!-- Toggle button — always visible on the left edge of the panel area -->
				<button
					class="absolute -left-3 top-4 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-surface-600 bg-surface-800 text-surface-400 shadow-md transition-colors hover:bg-surface-700 hover:text-surface-200"
					onclick={() => { if (detailPanelOpen) closeDetailPanel(); else openDetailPanel(); }}
					title={detailPanelOpen ? 'Collapse detail panel' : 'Expand detail panel'}
				>
					<svg class="h-3 w-3 transition-transform {detailPanelOpen ? '' : 'rotate-180'}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
					</svg>
				</button>

				<!-- Sliding panel -->
				<div
					class="overflow-hidden transition-[width] duration-300 ease-in-out {detailPanelOpen ? 'w-[420px]' : 'w-0'}"
				>
					<div class="w-[420px] h-full overflow-y-auto">
						{#if selectedCard}
							<CardDetail card={selectedCard} onclose={closeDetailPanel} ondismiss={dismissActiveCard} ongotocard={gotoCard} />
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
			</div>
		{/if}
	</div>
</div>

<!-- One-time integrations setup popup -->
{#if showIntegrationsPopup}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
		onkeydown={(e) => { if (e.key === 'Escape') showIntegrationsPopup = false; }}
	>
		<div class="relative mx-4 w-full max-w-sm rounded-xl border border-surface-700 bg-surface-800 p-6 shadow-2xl">
			<button
				onclick={() => showIntegrationsPopup = false}
				class="absolute right-3 top-3 text-surface-500 hover:text-surface-300 transition-colors"
				aria-label="Close"
			>
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>

			<div class="flex items-start gap-3">
				<div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-laya-orange/10">
					<svg class="h-5 w-5 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
					</svg>
				</div>
				<div>
					<h3 class="text-sm font-semibold text-surface-100">Set up your integrations</h3>
					<p class="mt-1.5 text-xs leading-relaxed text-surface-400">
						Connect your tools to start receiving cards. Set up Gmail, Jira, Slack, GitHub, and more from the Integrations settings.
					</p>
				</div>
			</div>

			<div class="mt-5 flex items-center gap-2">
				<a
					href="/settings?tab=integrations"
					onclick={() => showIntegrationsPopup = false}
					class="inline-flex items-center gap-1.5 rounded-md bg-laya-orange px-4 py-2 text-xs font-medium text-surface-900 transition-colors hover:bg-laya-gold"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
					</svg>
					Open Integrations
				</a>
				<button
					onclick={() => showIntegrationsPopup = false}
					class="rounded-md px-4 py-2 text-xs text-surface-400 transition-colors hover:text-surface-200"
				>
					Later
				</button>
			</div>
		</div>
	</div>
{/if}
