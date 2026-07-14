<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onMount, tick, untrack } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { parseBackendDate } from '$lib/utils/datetime';
	import { capturePositions, playFlip } from '$lib/utils/flip';
	import { lastMessage, wsStatus } from '$lib/stores/websocket';
	import { feedFilters, feedDate, feedPrevDate, feedNextDate, localToday, allDaysSavedDate, type FeedFilters } from '$lib/stores/feedFilters';
	import type { ActionCard, CardGroup, GroupSummary, DaySummary, SpaceSummary, Tag } from '$lib/api/types';
	import { reduceCardUpdated, removeCardFromGroups, type CardUpdatePayload } from '$lib/feed/cardUpdateReducer';
	import CardGroupComponent from '$lib/components/feed/CardGroup.svelte';
	import ActionCardComponent from '$lib/components/feed/ActionCard.svelte';
	import CardDetail from '$lib/components/feed/CardDetail.svelte';
	import GroupSummaryDetail from '$lib/components/feed/GroupSummaryDetail.svelte';
	import SummaryModal from '$lib/components/feed/SummaryModal.svelte';
	import FilterPopover from '$lib/components/feed/FilterPopover.svelte';
	import { feedViewMode } from '$lib/stores/feedView';
	import { feedSelection } from '$lib/stores/feedSelection';
	import ListRow from '$lib/components/feed/ListRow.svelte';
	import ListGroupComponent from '$lib/components/feed/ListGroup.svelte';
	import BulkActionsDropdown from '$lib/components/feed/BulkActionsDropdown.svelte';
	import LinkDialog from '$lib/components/feed/LinkDialog.svelte';
	import { recentCards, recentDrawerOpen, trackCardVisit, trackGroupVisit, type RecentCardEntry } from '$lib/stores/recentCards';
	import RecentDrawer from '$lib/components/feed/RecentDrawer.svelte';
	import { pendingCardId } from '$lib/stores/chat';
	import { spaces } from '$lib/stores/spaces';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { fade, slide } from 'svelte/transition';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { detailExpanded } from '$lib/stores/detailPanel';
	import { summaryModalOpen as summaryModalStore } from '$lib/stores/summaryModal';
	import { searchFocusSignal, feedSearchQuery } from '$lib/stores/searchFocus';
	import { portal } from '$lib/actions/portal';

	// Filter toolbar state
	let filterPopoverOpen = $state(false);
	let filterBtnEl: HTMLElement | undefined = $state();
	let filterMenuPos = $state({ top: 0, left: 0 });

	// Responsive toolbar: collapse action buttons into overflow menu when toolbar is too narrow
	let toolbarEl: HTMLElement | undefined = $state();
	let feedActionsCollapsed = $state(false);
	let feedActionsMenuOpen = $state(false);
	let feedActionsCollapseWidth = 0;
	const activeStatusCount = $derived($feedFilters.statusFilters.length);
	const activePriorityCount = $derived($feedFilters.priorityFilters.length);
	const activeSpaceCount = $derived($feedFilters.spaceFilter.length);
	const hasActiveFilters = $derived(activeStatusCount > 0 || activePriorityCount > 0 || $feedFilters.showArchived || $feedFilters.hasWorkspace || $feedFilters.showUnreadOnly || activeSpaceCount > 0);

	function closeFilterDropdown(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (!target.isConnected) return;
		// The FilterPopover keeps the `filter-dropdown` class on its (portaled)
		// root, so this class check alone recognises clicks inside it.
		if (!target.closest('.filter-dropdown') && !target.closest('.feed-overflow-menu')) {
			filterPopoverOpen = false;
		}
		if (!target.closest('.feed-overflow-menu')) {
			feedActionsMenuOpen = false;
		}
	}

	$effect(() => {
		document.addEventListener('click', closeFilterDropdown);
		return () => document.removeEventListener('click', closeFilterDropdown);
	});

	$effect(() => {
		if (!toolbarEl) return;
		function check() {
			const toolbarWidth = toolbarEl!.getBoundingClientRect().width;
			if (!feedActionsCollapsed) {
				const spacer = toolbarEl!.querySelector('[data-feed-spacer]');
				if (spacer && spacer.getBoundingClientRect().width < 4) {
					feedActionsCollapsed = true;
					feedActionsCollapseWidth = toolbarWidth;
					feedActionsMenuOpen = false;
				}
			} else {
				if (toolbarWidth > feedActionsCollapseWidth + 60) {
					feedActionsCollapsed = false;
					feedActionsMenuOpen = false;
				}
			}
		}
		const observer = new ResizeObserver(check);
		observer.observe(toolbarEl);
		return () => observer.disconnect();
	});

	let groups = $state<CardGroup[]>([]);
	let totalGroups = $state(0);
	// Group pagination (P4-9): the feed loads PAGE_SIZE groups at a time and
	// appends more on demand, so the unbounded modes no longer ship every group.
	const GROUPS_PAGE_SIZE = 200;
	let hasMoreGroups = $state(false);
	let loadingMoreGroups = $state(false);
	let availableTags = $state<Tag[]>([]);
	let loading = $state(true);
	let markingAllRead = $state(false);
	let error = $state<string | null>(null);
	let selectedCard = $state<ActionCard | null>(null);
	let selectedGroupSummary = $state<{ summary: GroupSummary | null; group: CardGroup } | null>(null);
	let generatingEntityIds = $state(new Set<string>());
	let detailPanelOpen = $state(false);
	let showIntegrationsPopup = $state(false);
	// Tracks the last card shown in the detail panel — survives dismiss so close can scroll back
	let lastDetailCardId = $state<string | null>(null);
	// Tracks the last group shown in the detail panel — used for scroll-back on close
	let lastDetailEntityId = $state<string | null>(null);
	// Persists after panel close so the card/group keeps a visual accent bar
	let lastViewedCardId = $state<string | null>(null);
	let lastViewedEntityId = $state<string | null>(null);

	// Link dialog state
	let linkSourceGroup = $state<CardGroup | null>(null);


	const hasAnySelection = $derived(!!selectedCard || !!selectedGroupSummary);
	const selectedEntityId = $derived(selectedGroupSummary?.group.entity_id ?? '');

	// Collapse the focus-mode overlay whenever nothing is selected (panel closed,
	// active card/group dismissed, or the card was deleted via WS). Mirrors the
	// chat sidebar resetting chatExpanded on close, so the next detail always
	// opens in the default inline layout. The expand state is intentionally ephemeral.
	$effect(() => {
		if (!hasAnySelection) detailExpanded.set(false);
	});

	// Focus-mode overlay geometry. Unlike the chat sidebar (which spans the whole
	// content band), the expanded description panel keeps the SAME top/bottom/right
	// as the inline panel — so it reads as the inline panel widening over the cards,
	// not a new full-height panel. We measure the inline slot (always mounted, and
	// kept at its reserved width while expanded) and pin the overlay to that box.
	// top/bottom/right are all scroll- and open-animation-invariant (the slot is a
	// right-anchored flex child whose height tracks the row and whose only animated
	// dimension is width), so measuring any time the panel is open is accurate.
	let detailSlotEl: HTMLElement | undefined = $state();
	let overlayGeom = $state<{ top: number; bottom: number; right: number; width: number } | null>(null);

	// Full width of the expanded overlay — mirrors the chat sidebar's
	// w-[75vw] min-w-[460px] max-w-[1100px] clamp, computed so we can animate a
	// width grow from the inline 420px to this value (natural expand, no reflow jump).
	function expandedOverlayWidth(): number {
		return Math.min(Math.max(Math.round(window.innerWidth * 0.75), 460), 1100);
	}

	function measureOverlayGeom() {
		if (!detailSlotEl) return;
		const r = detailSlotEl.getBoundingClientRect();
		overlayGeom = {
			top: Math.round(r.top),
			bottom: Math.round(Math.max(0, window.innerHeight - r.bottom)),
			right: Math.round(Math.max(0, window.innerWidth - r.right)),
			width: expandedOverlayWidth()
		};
	}

	// Keep the geometry measured whenever a detail panel is open — i.e. BEFORE the
	// user clicks expand — so the overlay mounts at the correct box with no flash and
	// the width-grow transition starts from the right anchor. Re-measure on resize.
	// We deliberately DON'T reset overlayGeom to null on close: the collapse outro
	// transition reads overlayGeom.width after the selection may already have cleared
	// (e.g. dismiss-while-expanded), and a null there would throw. The `{#if}` gate
	// keeps a stale value from ever being shown; it's refreshed on the next open.
	$effect(() => {
		if (!detailPanelOpen || !hasAnySelection) return;
		measureOverlayGeom();
		const onResize = () => measureOverlayGeom();
		window.addEventListener('resize', onResize);
		return () => window.removeEventListener('resize', onResize);
	});

	// Custom transition: grow/shrink the overlay width between the inline panel width
	// and the full focus-mode width, anchored on the right edge. On collapse it shrinks
	// back to exactly the inline panel's box before unmounting, so the hand-off to the
	// re-rendered inline panel is seamless (no height/width jitter).
	function panelGrow(_node: HTMLElement, { start, full, duration }: { start: number; full: number; duration: number }) {
		return {
			duration,
			css: (t: number) => `width:${(start + (full - start) * t).toFixed(1)}px;`
		};
	}

	// A card is standalone if its group has exactly 1 card and no context_id
	const selectedCardIsStandalone = $derived.by(() => {
		if (!selectedCard) return false;
		const group = groups.find(g =>
			g.cards.some(c => c.card_id === selectedCard!.card_id)
		);
		return group ? group.card_count === 1 : false;
	});

	function handleLinkGroup(group: CardGroup) {
		linkSourceGroup = group;
	}

	function handleLinkCard(card: ActionCard) {
		// Wrap standalone card as a single-card group for the link dialog
		const syntheticGroup: CardGroup = {
			entity_id: card.entity_id ?? card.card_id,
			entity_title: card.header,
			platform: card.space_name ?? '',
			card_count: 1,
			top_priority: card.priority as CardGroup['top_priority'],
			latest_at: card.created_at ?? '',
			has_pending: ['pending', 'ready'].includes(card.status),
			unread_count: card.read_at ? 0 : 1,
			cards: [card],
			context_id: card.context_id,
		};
		linkSourceGroup = syntheticGroup;
	}

	async function handleShowRelated(card: ActionCard) {
		try {
			const data = await engineApi.getRelatedCards(card.card_id);
			if (data.total_related_cards === 0) return;
			const entityIds = [...new Set([
				card.entity_id,
				...data.related_cards.map((r: { entity_id: string }) => r.entity_id)
			].filter(Boolean))] as string[];
			$feedFilters.showRelated = true;
			$feedFilters.relatedEntityIds = entityIds;
			$feedFilters.relatedSourceHeader = card.header;
			$feedFilters.relatedSourceCardId = card.card_id;
			$feedFilters.relatedSourceEntityId = card.entity_id ?? '';
		} catch {
			// Silently fail
		}
	}

	let relatedViewExiting = $state(false);

	function clearRelatedFilter(animated = false) {
		const doClear = () => {
			_scrollToCardId = selectedCard?.card_id ?? lastDetailCardId ?? lastViewedCardId ?? null;
			if (!_scrollToCardId) {
				_scrollToGroupEntityId = selectedGroupSummary?.group.entity_id ?? lastDetailEntityId ?? lastViewedEntityId ?? null;
			}
			relatedViewExiting = false;
			$feedFilters.showRelated = false;
			$feedFilters.relatedEntityIds = [];
			$feedFilters.relatedSourceHeader = '';
			$feedFilters.relatedSourceCardId = '';
			$feedFilters.relatedSourceEntityId = '';
		};
		if (animated && !$reducedMotion) {
			relatedViewExiting = true;
			closeDetail();
			setTimeout(doClear, 250);
		} else {
			doClear();
		}
	}

	async function handleUnlinked(cardId: string, entityId: string) {
		if (!$feedFilters.showRelated) return;
		const sourceCardId = $feedFilters.relatedSourceCardId;
		if (!sourceCardId) { clearRelatedFilter(true); return; }
		try {
			const data = await engineApi.getRelatedCards(sourceCardId);
			if (data.total_related_cards === 0) { clearRelatedFilter(true); return; }
			const sourceEntityId = $feedFilters.relatedSourceEntityId;
			const entityIds = [...new Set([
				sourceEntityId,
				...data.related_cards.map((r: { entity_id: string }) => r.entity_id)
			].filter(Boolean))] as string[];
			$feedFilters.relatedEntityIds = entityIds;
		} catch {
			clearRelatedFilter(true);
		}
	}

	async function handleBulkUnlinked(cardIds: string[]) {
		if (!$feedFilters.showRelated) return;
		const sourceCardId = $feedFilters.relatedSourceCardId;
		if (sourceCardId && cardIds.includes(sourceCardId)) {
			clearRelatedFilter(true);
			return;
		}
		if (!sourceCardId) { clearRelatedFilter(true); return; }
		try {
			const data = await engineApi.getRelatedCards(sourceCardId);
			if (data.total_related_cards === 0) { clearRelatedFilter(true); return; }
			const sourceEntityId = $feedFilters.relatedSourceEntityId;
			const entityIds = [...new Set([
				sourceEntityId,
				...data.related_cards.map((r: { entity_id: string }) => r.entity_id)
			].filter(Boolean))] as string[];
			$feedFilters.relatedEntityIds = entityIds;
		} catch {
			clearRelatedFilter(true);
		}
	}


	// When switching views, scroll the active card/group into view.
	$effect(() => {
		const _mode = $feedViewMode;
		tick().then(() => {
			if (selectedCard) {
				scrollToCard(selectedCard.card_id);
			} else if (lastViewedEntityId) {
				scrollToGroupElement(lastViewedEntityId);
			} else if (lastViewedCardId) {
				scrollToCard(lastViewedCardId);
			}
		});
	});

	// Auto-open detail panel when a card is selected (skip FLIP — scroll will follow)
	$effect(() => {
		if (selectedCard) openDetailPanel(true);
	});

	// Search state — persisted in module-level store so it survives navigation
	let searchQuery = $state($feedSearchQuery);
	let searchInputEl: HTMLInputElement | undefined = $state();

	// Tag autocomplete state
	let showTagAutocomplete = $state(false);
	let tagAutocompletePos = $state({ top: 0, left: 0 });
	let tagAutocompleteIdx = $state(0);

	function getCurrentWord(input: HTMLInputElement): string {
		const pos = input.selectionStart ?? input.value.length;
		const before = input.value.slice(0, pos);
		const match = before.match(/(\S+)$/);
		return match ? match[1] : '';
	}

	const tagAutocompleteQuery = $derived.by(() => {
		if (!showTagAutocomplete) return '';
		const tokens = searchQuery.trim().split(/\s+/);
		const last = tokens[tokens.length - 1] || '';
		if (last.startsWith('#')) return last.slice(1).toLowerCase();
		return '';
	});

	const tagSuggestions = $derived(
		tagAutocompleteQuery
			? availableTags
				.filter(t => t.name.toLowerCase().includes(tagAutocompleteQuery) && !activeSearchTags.includes(t.name))
				.slice(0, 6)
			: []
	);

	function insertTagToken(name: string) {
		const parts = searchQuery.trimEnd().split(/\s+/);
		parts[parts.length - 1] = '#' + name;
		searchQuery = parts.join(' ') + ' ';
		showTagAutocomplete = false;
		tagAutocompleteIdx = 0;
		searchInputEl?.focus();
	}

	function removeSearchTag(name: string) {
		searchQuery = searchQuery.replace(new RegExp(`#${name}\\b\\s*`, 'g'), '').trim();
	}

	function updateTagAutocomplete() {
		if (!searchInputEl) return;
		const word = getCurrentWord(searchInputEl);
		if (word.startsWith('#')) {
			showTagAutocomplete = true;
			const r = searchInputEl.getBoundingClientRect();
			tagAutocompletePos = { top: r.bottom + 4, left: r.left };
			tagAutocompleteIdx = 0;
		} else {
			showTagAutocomplete = false;
		}
	}

	$effect(() => {
		feedSearchQuery.set(searchQuery);
	});


	$effect(() => {
		if ($searchFocusSignal && searchInputEl) {
			searchInputEl.focus();
			searchInputEl.select();
			searchFocusSignal.set(0);
		}
	});

	// Summary modal state — backed by global store so keyboard shortcut can toggle it
	let summaryModalOpen = $state(false);
	$effect(() => { summaryModalOpen = $summaryModalStore; });
	function setSummaryModalOpen(v: boolean) { summaryModalOpen = v; summaryModalStore.set(v); }
	// Load summary when opened via keyboard shortcut
	$effect(() => { if ($summaryModalStore) loadSummary(); });
	let spaceSummaries = $state<SpaceSummary[]>([]);
	let daySummary = $state<DaySummary | null>(null);
	let summaryUpdatedAt = $state<string | null>(null);
	let summaryLoading = $state(false);

	function mergeSpaceSummaries(summaries: SpaceSummary[]): { merged: DaySummary; updatedAt: string | null } {
		const merged: DaySummary = { events_and_meetings: [], action_items: [], key_updates: [] };
		let latest: string | null = null;
		for (const ss of summaries) {
			if (!ss.summary) continue;
			if (ss.updated_at && (!latest || ss.updated_at > latest)) latest = ss.updated_at;
			for (const section of ['events_and_meetings', 'action_items', 'key_updates'] as const) {
				for (const item of ss.summary[section]) {
					merged[section].push({
						...item,
						space_id: ss.space_id,
						space_name: ss.space_name,
						space_color: ss.space_color,
					});
				}
			}
		}
		return { merged, updatedAt: latest };
	}

	// Persist selected card/group ID across webview navigations (e.g. external link → back)
	const SELECTED_CARD_KEY = 'laya_feed_selected_card';
	const SELECTED_GROUP_KEY = 'laya_feed_selected_group';

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
	let _skipNextFlip = false;

	// Capture card positions for FLIP animation before data changes
	function captureCardPositions(): Map<string, DOMRect> {
		return capturePositions(containerEl, '[data-entity-id]', (el) => el.dataset.entityId);
	}

	// Apply FLIP animation from old positions to current positions
	// When instant=true, skip animations (used for panel open/close to avoid jarring double transitions).
	// Reduced motion takes the same early-exit path so columns repack without any translate/fade.
	async function animateFlip(oldPositions: Map<string, DOMRect>, instant = false) {
		if (!containerEl || oldPositions.size === 0) return;
		if (instant || $reducedMotion) {
			_flipSettled = Promise.resolve();
			return;
		}
		// Signal that a FLIP animation is in progress; resolves after animations finish.
		// (Owned here, not in playFlip, so instant / reduced-motion runs resolve it
		// immediately above without a wasted 350ms wait.)
		_flipSettled = new Promise((resolve) => setTimeout(resolve, 350));
		await playFlip(containerEl, '[data-entity-id]', (el) => el.dataset.entityId, oldPositions, {
			axis: 'xy',
			durationMs: 300,
			animateEntrance: true,
			entranceDurationMs: 250,
		});
	}

	// Build the /cards/grouped query params shared by loadGroups and loadMoreGroups
	// (minus limit/offset, which the caller sets). Pure of the fetch itself so the
	// two paths can't drift.
	function buildGroupedParams(f: FeedFilters, date: string) {
		// Read searchQuery WITHOUT tracking it. loadGroups() runs synchronously
		// inside the $feedDate/$feedFilters reload effect, so a tracked read here
		// silently made searchQuery a dependency of that effect — every keystroke
		// re-ran it and fired a full GET /cards/grouped, defeating the 300ms search
		// debounce (review §2 UI — P4-29). Backend search only applies in all-days
		// mode (see the search/tags params below), which reloads via its own
		// debounced effect; normal-mode search is filtered client-side.
		const _tagTokens: string[] = [];
		const _textTokens: string[] = [];
		untrack(() => {
			for (const token of searchQuery.trim().split(/\s+/)) {
				if (token.startsWith('#')) {
					// Bare '#' is a half-typed tag, not a search for a literal '#'.
					if (token.length > 1) _tagTokens.push(token.slice(1));
				} else if (token) {
					_textTokens.push(token);
				}
			}
		});
		const _searchText = _textTokens.join(' ');
		const isAllDays = f.showAllDaysSearch;
		return {
			status: f.statusFilters.length ? f.statusFilters.join(',') : undefined,
			priority: f.priorityFilters.length ? f.priorityFilters.join(',') : undefined,
			sort: f.sortBy,
			sort_asc: f.sortAsc || undefined,
			show_archived: f.showArchived || undefined,
			date: (f.showBookmarked || f.showRelated || isAllDays) ? undefined : date,
			space_id: f.spaceFilter.length ? f.spaceFilter.join(',') : undefined,
			bookmarked: f.showBookmarked || undefined,
			related_entity_ids: f.showRelated ? f.relatedEntityIds.join(',') : undefined,
			has_workspace: f.hasWorkspace || undefined,
			unread_only: f.showUnreadOnly || undefined,
			search: isAllDays && _searchText ? _searchText : undefined,
			tags: isAllDays && _tagTokens.length ? _tagTokens.join(',') : undefined
		};
	}

	// Fetch the next page of groups and APPEND (dedup by entity_id — offset paging
	// over a live, re-sorting list can re-surface a group). Never resets the list,
	// so it doesn't disturb the current scroll position or selection (P4-9).
	async function loadMoreGroups() {
		if (loadingMoreGroups || !hasMoreGroups) return;
		loadingMoreGroups = true;
		try {
			const data = await engineApi.getGroupedCards({
				...buildGroupedParams($feedFilters, $feedDate),
				limit: GROUPS_PAGE_SIZE,
				offset: groups.length
			});
			const seen = new Set(groups.map((g) => g.entity_id));
			const fresh = data.groups.filter((g) => !seen.has(g.entity_id));
			groups = [...groups, ...fresh];
			totalGroups = data.total_groups;
			hasMoreGroups = data.has_more ?? false;
		} catch {
			// Best-effort — leave the current list intact on failure.
		} finally {
			loadingMoreGroups = false;
		}
	}

	async function loadGroups() {
		const id = ++_fetchId;
		loading = true;
		error = null;
		try {
			const f = $feedFilters;
			const data = await engineApi.getGroupedCards({
				...buildGroupedParams(f, $feedDate),
				limit: GROUPS_PAGE_SIZE,
				offset: 0
			});
			if (id !== _fetchId) return;

			// Capture positions before updating groups for FLIP animation
			const oldPositions = captureCardPositions();

			groups = data.groups;
			totalGroups = data.total_groups;
			hasMoreGroups = data.has_more ?? false;
			$feedPrevDate = data.prev_date ?? null;
			$feedNextDate = data.next_date ?? null;

			// Restore selected card or group after navigating back from an external page
			if (!selectedCard && !selectedGroupSummary) {
				const savedCardId = sessionStorage.getItem(SELECTED_CARD_KEY);
				const savedGroupId = sessionStorage.getItem(SELECTED_GROUP_KEY);
				if (savedCardId) {
					for (const g of data.groups) {
						const found = g.cards.find((c) => c.card_id === savedCardId);
						if (found) {
							selectedCard = found;
							// The grouped payload is slimmed (no staged_output/suggested_actions
							// — P4-9); unlike selectCard(), this restore path never hydrated, so
							// the detail panel would render permanently without them. Re-fetch.
							engineApi.getCard(found.card_id).then((fresh) => {
								if (selectedCard?.card_id === found.card_id) selectedCard = fresh as ActionCard;
							}).catch(() => {});
							break;
						}
					}
				} else if (savedGroupId) {
					const g = data.groups.find((g) => g.entity_id === savedGroupId);
					if (g) {
						selectedGroupSummary = { summary: g.group_summary ?? null, group: g };
						lastDetailEntityId = g.entity_id;
						lastViewedEntityId = g.entity_id;
						openDetailPanel(true);
						_scrollToGroupEntityId = g.entity_id;
					}
				}
			}

			// FLIP animate existing cards + fade in new ones.
			// Must run before scrollToCard so that scrollToCard captures the
			// new _flipSettled promise and waits for the animation to finish
			// before attempting to scroll.
			const skipFlip = _skipNextFlip;
			_skipNextFlip = false;
			animateFlip(oldPositions, skipFlip);

			// Scroll to card/group if requested (e.g. gotoCard, clearRelatedFilter)
			if (_scrollToCardId) {
				scrollToCard(_scrollToCardId);
			} else if (_scrollToGroupEntityId) {
				scrollToGroupElement(_scrollToGroupEntityId);
				_scrollToGroupEntityId = null;
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
			spaceSummaries = data.space_summaries;
			const { merged, updatedAt } = mergeSpaceSummaries(spaceSummaries);
			daySummary = merged;
			summaryUpdatedAt = updatedAt;
		} catch {
			spaceSummaries = [];
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

	// Resync after a WebSocket reconnect. The store wipes its buffered messages on
	// disconnect, so after an engine restart the feed would otherwise stay silently
	// stale until the user changed a filter (review §2 UI — P4-30). Fire only on a
	// genuine reconnect (2nd+ time we reach 'connected'), not the initial connect —
	// the mount-time reload effect above already fetched.
	let _hasConnectedBefore = false;
	$effect(() => {
		if ($wsStatus === 'connected') {
			if (_hasConnectedBefore) scheduleReload();
			_hasConnectedBefore = true;
		}
	});

	// Load available tags for the filter
	$effect(() => {
		engineApi.listTags().then(r => { availableTags = r.tags; }).catch(() => {});
	});

	// Re-query backend when search changes in all-days mode
	$effect(() => {
		if ($feedFilters.showAllDaysSearch) {
			searchQuery;
			scheduleReload();
		}
	});

	// Exit all-days search when search is cleared
	$effect(() => {
		if (!searchActive && $feedFilters.showAllDaysSearch) {
			$feedDate = $allDaysSavedDate;
			$feedFilters = { ...$feedFilters, showAllDaysSearch: false };
		}
	});

	// Reload the summary only when the DATE changes while the modal is open.
	// summaryModalOpen is read untracked so the modal-OPEN transition doesn't also
	// trigger here — the effect above ($summaryModalStore → loadSummary) already
	// owns "load on open". Both firing (plus the button calling loadSummary
	// directly) caused 2-3 duplicate fetches per open (review §2 UI — P4-34).
	$effect(() => {
		$feedDate;
		if (untrack(() => summaryModalOpen)) loadSummary();
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

		// Handle per-space summary updates
		if (msg.type === 'summary_updated' && msg.payload) {
			_lastProcessedMsg = msg;
			const payload = msg.payload as { date?: string; space_id?: string; summary?: DaySummary; updated_at?: string };
			if (payload.summary && payload.space_id && (payload.date === $feedDate || isToday)) {
				const idx = spaceSummaries.findIndex(s => s.space_id === payload.space_id);
				const entry: SpaceSummary = {
					space_id: payload.space_id,
					space_name: spaceSummaries.find(s => s.space_id === payload.space_id)?.space_name ?? payload.space_id,
					space_color: spaceSummaries.find(s => s.space_id === payload.space_id)?.space_color ?? '#F97316',
					summary: payload.summary,
					card_ids: [],
					updated_at: payload.updated_at ?? null,
				};
				if (idx >= 0) {
					entry.card_ids = spaceSummaries[idx].card_ids;
					entry.space_name = spaceSummaries[idx].space_name;
					entry.space_color = spaceSummaries[idx].space_color;
					spaceSummaries = [...spaceSummaries.slice(0, idx), entry, ...spaceSummaries.slice(idx + 1)];
				} else {
					spaceSummaries = [...spaceSummaries, entry];
				}
				const { merged, updatedAt } = mergeSpaceSummaries(spaceSummaries);
				daySummary = merged;
				summaryUpdatedAt = updatedAt;
			}
			return;
		}

		if (msg.type === 'group_summary_updated' && msg.entity_id) {
			_lastProcessedMsg = msg;
			const updatedSummary = msg.summary as GroupSummary;
			groups = groups.map(g => {
				if (g.entity_id === msg.entity_id) {
					return { ...g, group_summary: updatedSummary };
				}
				if (g.sub_groups) {
					return { ...g, sub_groups: g.sub_groups.map(sg =>
						sg.entity_id === msg.entity_id
							? { ...sg, group_summary: updatedSummary }
							: sg
					)};
				}
				return g;
			});
			if (generatingEntityIds.has(msg.entity_id)) {
				generatingEntityIds.delete(msg.entity_id);
				generatingEntityIds = new Set(generatingEntityIds);
			}
			if (selectedGroupSummary && selectedGroupSummary.group.entity_id === msg.entity_id) {
				const updatedGroup = groups.find(g => g.entity_id === msg.entity_id)
					?? groups.flatMap(g => g.sub_groups ?? []).find(sg => sg.entity_id === msg.entity_id);
				if (updatedGroup) {
					selectedGroupSummary = { summary: updatedSummary, group: updatedGroup };
				}
			}
			return;
		}

		if (msg.type === 'tags_changed') {
			_lastProcessedMsg = msg;
			engineApi.listTags().then(r => { availableTags = r.tags; }).catch(() => {});
			const p = msg.payload as { target_type?: string; target_id?: string } | undefined;
			if (p?.target_type === 'card' && p.target_id) {
				const cardId = p.target_id;
				engineApi.getTagsFor('card', cardId).then(r => {
					const tags = r.tags;
					groups = groups.map(g => ({
						...g,
						cards: g.cards.map(c =>
							c.card_id === cardId ? { ...c, tags } : c
						),
						sub_groups: g.sub_groups?.map(sg => ({
							...sg,
							cards: sg.cards.map(c =>
								c.card_id === cardId ? { ...c, tags } : c
							),
						})),
					}));
					if (selectedCard?.card_id === cardId) {
						selectedCard = { ...selectedCard, tags };
					}
				}).catch(() => {});
			}
			return;
		}

		if (!['card_created', 'card_deleted', 'card_updated', 'group_carried_forward', 'context_group_unlinked', 'context_group_merged', 'action_payload_updated'].includes(msg.type)) return;
		_lastProcessedMsg = msg;

		if (msg.type === 'action_payload_updated' && msg.card_id) {
			// Merge the updated action payload into the open detail card so CardDetail
			// reflects fresh state (polish result, _polishing spinner, _edited flag).
			// Only selectedCard is patched now: the grouped list payload is slimmed of
			// suggested_actions (P4-9) and nothing in the list renders them, so there's
			// no grouped-card copy to keep in sync.
			const actionId = (msg as { action_id?: string }).action_id;
			const newPayload = (msg.payload as { payload?: Record<string, unknown> })?.payload;
			if (actionId && newPayload && selectedCard?.card_id === msg.card_id && selectedCard.suggested_actions) {
				const action = selectedCard.suggested_actions.find((a) => a.action_id === actionId);
				if (action) {
					action.payload = { ...action.payload, ...newPayload };
					selectedCard = { ...selectedCard };
				}
			}
			return;
		}

		if (msg.type === 'card_created' || msg.type === 'group_carried_forward') {
			// Only auto-reload if viewing today (new cards / carried-forward groups land on today)
			if (isToday) scheduleReload();
		} else if (msg.type === 'card_deleted' && msg.card_id) {
			if (selectedCard?.card_id === msg.card_id) {
				selectedCard = null;
				sessionStorage.removeItem(SELECTED_CARD_KEY);
			}
			feedSelection.removeDeleted(msg.card_id);
			groups = removeCardFromGroups(groups, msg.card_id);
			totalGroups = groups.length;
		} else if (msg.type === 'card_updated' && msg.card_id) {
			// Pure reducer decides the next state + the side effects; the loop
			// below runs them. See cardUpdateReducer.ts for the extracted logic
			// (P7-7) and its unit tests.
			const result = reduceCardUpdated(
				groups,
				selectedCard,
				msg.card_id,
				msg.payload as CardUpdatePayload,
				{
					statusFilters: $feedFilters.statusFilters,
					showArchived: $feedFilters.showArchived,
					sortBy: $feedFilters.sortBy,
					spaceFilter: $feedFilters.spaceFilter,
				}
			);
			groups = result.groups;
			selectedCard = result.selectedCard;
			for (const eff of result.effects) {
				if (eff.kind === 'reload') {
					scheduleReload();
				} else if (eff.kind === 'removeFromSelection') {
					feedSelection.removeDeleted(eff.cardId);
				} else if (eff.kind === 'deselect') {
					sessionStorage.removeItem(SELECTED_CARD_KEY);
				} else if (eff.kind === 'exitThenRemove') {
					const id = eff.cardId;
					// Mark the card exiting to trigger the fade-out, then drop it once
					// the transition completes (loadGroups' FLIP animates the reflow).
					exitingCardIds = new Set([...exitingCardIds, id]);
					setTimeout(() => {
						groups = removeCardFromGroups(groups, id);
						totalGroups = groups.length;
						exitingCardIds = new Set([...exitingCardIds].filter((x) => x !== id));
					}, EXIT_DURATION);
				}
			}
		} else if (msg.type === 'context_group_unlinked' || msg.type === 'context_group_merged') {
			// Context group structure changed — reload to reflect new grouping
			scheduleReload();
		}
	});

	// Cards exiting due to filter mismatch — tracked for fade-out transition
	let exitingCardIds = $state(new Set<string>());
	const EXIT_DURATION = 250; // ms — fast but visible

	// ID of card to scroll into view after next render (set by gotoCard)
	let _scrollToCardId = $state<string | null>(null);
	// Entity ID of group to scroll into view after next render (set by clearRelatedFilter for groups)
	let _scrollToGroupEntityId = $state<string | null>(null);

	function gotoCard(card: ActionCard) {
		let cardDate: string | null = null;
		// Use group_active_at (carry-forward date) if available, otherwise fall back to created_at.
		// group_active_at reflects when the card's entity group was last active, which is the
		// date the card will actually appear on in the feed due to group carry-forward.
		const d = parseBackendDate(card.group_active_at || card.created_at);
		if (d) {
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

	// Find the DOM element for a card. Prefers the individual ActionCard element
	// over the group wrapper — the group wrapper also carries data-card-id for
	// its topCard, but is ~11k px tall for large groups so scrolling to it
	// centers the group, not the card. Falls back to the group wrapper when
	// the group is collapsed (individual card not rendered).
	function findCardElement(cardId: string): Element | null {
		return document.querySelector(`[data-card-id="${cardId}"]:not([data-group-entity])`)
			?? document.querySelector(`[data-card-id="${cardId}"]`);
	}

	// Walk up the DOM to find the nearest scrollable ancestor (overflow auto/scroll
	// with content that actually overflows). This avoids assuming which container
	// scrolls — the layout has nested overflow-auto on both <main> and containerEl.
	function getScrollParent(el: Element): Element {
		let parent = el.parentElement;
		while (parent) {
			const { overflowY } = getComputedStyle(parent);
			if ((overflowY === 'auto' || overflowY === 'scroll') && parent.scrollHeight > parent.clientHeight) {
				return parent;
			}
			parent = parent.parentElement;
		}
		return document.documentElement;
	}

	// Scroll an element to the vertical center of its nearest scrollable ancestor.
	// Uses manual scrollTo on the specific container to avoid scrollIntoView's
	// nested scroll container bug (it scrolls ALL overflow ancestors unpredictably).
	function scrollElToCenter(el: Element, behavior: ScrollBehavior = 'smooth') {
		const scroller = getScrollParent(el);
		const scrollerRect = scroller.getBoundingClientRect();
		const elRect = el.getBoundingClientRect();
		const targetTop = scroller.scrollTop + (elRect.top - scrollerRect.top) - (scrollerRect.height - elRect.height) / 2;
		scroller.scrollTo({ top: targetTop, behavior });
	}

	function scrollToCard(cardId: string) {
		_scrollToCardId = cardId;
		// Wait for any in-progress FLIP animation to finish, then scroll
		_flipSettled.then(() => {
			// Retry up to 15 times (~250ms) to allow group slide transition (200ms) to complete
			const attempt = (tries: number) => {
				requestAnimationFrame(() => {
					const el = findCardElement(cardId);
					if (el) {
						scrollElToCenter(el);
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
			attempt(15);
		});
	}

	function scrollToGroupElement(entityId: string) {
		_flipSettled.then(() => {
			const attempt = (tries: number) => {
				requestAnimationFrame(() => {
					const el = document.querySelector(`[data-group-entity="${entityId}"]`);
					if (el) {
						scrollElToCenter(el);
						const rowEl = document.querySelector(`[data-group-row="${entityId}"]`) ?? el;
						rowEl.classList.add('card-highlight-fade');
						rowEl.addEventListener('animationend', () => rowEl.classList.remove('card-highlight-fade'), { once: true });
					} else if (tries > 0) {
						attempt(tries - 1);
					}
				});
			};
			attempt(15);
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
		selectedGroupSummary = null;
		lastDetailEntityId = null;
		lastViewedEntityId = null;
		selectedCard = card;
		lastDetailCardId = card.card_id;
		lastViewedCardId = card.card_id;
		sessionStorage.setItem(SELECTED_CARD_KEY, card.card_id);
		sessionStorage.removeItem(SELECTED_GROUP_KEY);
		trackCardVisit(card);
		// Optimistic read marking
		if (!card.read_at) {
			card.read_at = new Date().toISOString();
			const group = groups.find(g => g.cards.some(c => c.card_id === card.card_id));
			if (group && group.unread_count > 0) group.unread_count--;
			groups = groups;
			engineApi.markCardRead(card.card_id).catch(() => {});
		}
		// Fetch full card from API to ensure suggested_actions and other fields
		// that may be missing from WS-patched data are present.
		engineApi.getCard(card.card_id).then((fresh) => {
			if (selectedCard?.card_id === card.card_id) {
				selectedCard = fresh as ActionCard;
			}
		}).catch(() => {});
	}

	function selectGroupSummary(group: CardGroup) {
		selectedCard = null;
		selectedGroupSummary = {
			summary: group.group_summary ?? null,
			group
		};
		lastDetailCardId = null;
		lastDetailEntityId = group.entity_id;
		lastViewedCardId = null;
		lastViewedEntityId = group.entity_id;
		sessionStorage.removeItem(SELECTED_CARD_KEY);
		sessionStorage.setItem(SELECTED_GROUP_KEY, group.entity_id);
		const firstCard = group.cards[0];
		trackGroupVisit({
			...group,
			space_id: firstCard?.space_id ?? undefined,
			space_name: firstCard?.space_name ?? undefined
		});
		openDetailPanel(true);
		scrollToGroupElement(group.entity_id);
		// Optimistic: mark all cards in group as read
		if (group.unread_count > 0) {
			const now = new Date().toISOString();
			for (const c of group.cards) {
				if (!c.read_at) c.read_at = now;
			}
			group.unread_count = 0;
			groups = groups;
			engineApi.markGroupRead(group.entity_id).catch(() => {});
		}
	}

	function closeDetail() {
		selectedCard = null;
		selectedGroupSummary = null;
		detailPanelOpen = false;
		lastDetailCardId = null;
		lastDetailEntityId = null;
		sessionStorage.removeItem(SELECTED_CARD_KEY);
		sessionStorage.removeItem(SELECTED_GROUP_KEY);
	}

	// Dismiss the active card without closing the panel (X button in CardDetail)
	function dismissActiveCard() {
		selectedCard = null;
		sessionStorage.removeItem(SELECTED_CARD_KEY);
	}

	// Dismiss the active group summary without closing the panel (X button in GroupSummaryDetail)
	function dismissActiveGroupSummary() {
		selectedGroupSummary = null;
		sessionStorage.removeItem(SELECTED_GROUP_KEY);
	}

	// Run Agent at entity level — invoked from GroupSummaryDetail or CardDetail (single-card entities)
	let runningAgentEntityId = $state<string | null>(null);
	async function handleRunEntityAgent(entityId: string) {
		runningAgentEntityId = entityId;
		try {
			await engineApi.runEntityAgent(entityId);
		} catch (e: any) {
			const detail = e?.body?.detail || e?.message || 'Failed to start agent';
			error = detail;
		} finally {
			runningAgentEntityId = null;
		}
	}

	// Check if selected card is a single-card entity (not part of a multi-card group)
	const selectedCardIsSingleEntity = $derived.by(() => {
		if (!selectedCard) return false;
		const entityId = selectedCard.entity_id;
		if (!entityId) return false;
		const group = groups.find((g) => g.entity_id === entityId);
		return group ? group.card_count === 1 : true;
	});

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
		// Close the summary modal and navigate to the card
		setSummaryModalOpen(false);
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
		// The RecentDrawer owns the reorder FLIP; this just navigates + reorders.
		if (entry.type === 'group') {
			const group = groups.find((g) => g.entity_id === entry.card_id);
			if (group) {
				selectGroupSummary(group);
				scrollToGroupElement(group.entity_id);
			}
			return;
		}
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

	const totalCards = $derived(groups.reduce((sum, g) => sum + g.card_count, 0));
	const agentRunningCount = $derived(
		groups.reduce((sum, g) => sum + g.cards.filter((c) => c.status === 'agent_running').length, 0)
	);
	const failedCount = $derived(
		groups.reduce((sum, g) => sum + g.cards.filter((c) => c.status === 'failed').length, 0)
	);

	const hasUnread = $derived(groups.some(g => g.unread_count > 0));

	async function handleMarkAllRead() {
		markingAllRead = true;
		try {
			const f = $feedFilters;
			await engineApi.markAllRead({
				date: (f.showBookmarked || f.showRelated || f.showAllDaysSearch) ? undefined : $feedDate,
				space_id: f.spaceFilter.length ? f.spaceFilter.join(',') : undefined,
			});
			const now = new Date().toISOString();
			for (const group of groups) {
				for (const card of group.cards) {
					if (!card.read_at) card.read_at = now;
				}
				group.unread_count = 0;
			}
			groups = groups;
		} finally {
			markingAllRead = false;
		}
	}

	// Search filtering. A `#tag` token is a TAG filter, never text: it matches only
	// cards carrying that tag (or whose entity group carries it), NOT cards whose
	// content happens to mention the word. This used to strip the '#' and fold the
	// tag into `terms`, so "#reviewer" substring-matched the content haystack and
	// surfaced every card merely mentioning a reviewer. Semantics mirror the
	// backend's search/tags split in api/cards_feed.py: tag tokens OR together,
	// text terms AND together, and the two sets AND with each other.
	function cardMatchesSearch(
		card: ActionCard,
		terms: string[],
		tagTerms: string[],
		groupTagNames: string[]
	): boolean {
		if (tagTerms.length > 0) {
			const cardTagNames = (card.tags ?? []).map((t) => t.tag_name.toLowerCase());
			// Entity-level tags apply to every card in the group (backend ORs the
			// card-tag and entity-tag subqueries the same way).
			const hasTag = tagTerms.some(
				(t) => cardTagNames.includes(t) || groupTagNames.includes(t)
			);
			if (!hasTag) return false;
		}
		if (terms.length === 0) return true;
		const privacyLabel = card.privacy_tier === 3 ? 'confidential' : card.privacy_tier === 2 ? 'internal' : card.privacy_tier === 1 ? 'public' : '';
		const searchable = [
			card.header,
			card.summary,
			card.category,
			card.entity_id,
			card.source_ref,
			card.actor_name,
			card.actor_email,
			card.space_name,
			card.persona,
			card.priority,
			card.status,
			privacyLabel,
			...(card.intelligence ?? []),
			// staged_output/suggested_actions are slimmed from the grouped payload
			// (P4-9) and are not scanned by the backend search either (P4-10), so the
			// client-side filter matches the same fields the server does.
			...(card.tags?.map((t) => t.tag_name) ?? [])
		]
			.filter(Boolean)
			.join(' ')
			.toLowerCase();
		return terms.every((term) => searchable.includes(term));
	}

	// Text terms only — `#tag` tokens are peeled off into activeSearchTags and must
	// NOT leak in here as text. Any '#'-prefixed token is dropped, including a lone
	// '#': that's the half-typed state before a tag name (autocomplete is open), so
	// it filters nothing rather than matching every card containing a literal '#'.
	const searchTerms = $derived(
		searchQuery.trim() === ''
			? []
			: searchQuery
					.toLowerCase()
					.split(/\s+/)
					.filter((t) => t && !t.startsWith('#'))
	);

	const activeSearchTags = $derived(
		searchQuery.trim().split(/\s+/).filter(t => t.startsWith('#') && t.length > 1).map(t => t.slice(1))
	);

	// Tag names are UNIQUE COLLATE NOCASE (migration 065), so compare lowercased.
	// Kept separate from activeSearchTags, which stays as-typed for the chips.
	const searchTagTerms = $derived(activeSearchTags.map((t) => t.toLowerCase()));

	// "Is search active?" — a pure "#tag" query has no text terms, so length checks
	// on searchTerms alone would read as "search cleared" and drop the tag filter.
	const searchActive = $derived(searchTerms.length > 0 || activeSearchTags.length > 0);

	const filteredGroups = $derived.by(() => {
		if (!searchActive) return groups;
		return groups
			.map((group) => {
				const groupTagNames = (group.tags ?? []).map((t) => t.tag_name.toLowerCase());
				const groupTagMatch =
					searchTagTerms.length === 0 || searchTagTerms.some((t) => groupTagNames.includes(t));
				// Check group-level fields first. The tag filter still has to hold —
				// otherwise a title merely containing the tag word would pull in the
				// whole group, which is the bug this guards.
				const groupText = [group.entity_title, group.platform].join(' ').toLowerCase();
				if (groupTagMatch && searchTerms.every((t) => groupText.includes(t))) return group;
				// Filter individual cards
				const matching = group.cards.filter((c) =>
					cardMatchesSearch(c, searchTerms, searchTagTerms, groupTagNames)
				);
				if (matching.length === 0) return null;
				return {
					...group,
					cards: matching,
					card_count: matching.length,
					has_pending: matching.some(
						(c) => c.status === 'pending' || c.status === 'ready'
					)
				};
			})
			.filter((g): g is CardGroup => g !== null);
	});

	const filteredDaySummary = $derived.by((): DaySummary | null => {
		if (!daySummary || !searchActive) return daySummary;
		// Summary items carry no tag assignments, so nothing in the summary can
		// satisfy a #tag filter — showing it unfiltered would imply it matched.
		if (searchTagTerms.length > 0) return null;
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
	// When true, the next ResizeObserver-triggered flipColumns uses instant
	// mode. Set before clearing panelTransitioning so the observer callback
	// that fires after containment removal doesn't run a FLIP animation.
	let _resizeInstant = false;
	// Last known scroll anchor (top-most visible feed item + its viewport offset),
	// tracked while scrolling so it reflects a STABLE layout. We can't measure it
	// inside the ResizeObserver because by then the container has already narrowed
	// while still showing the old column count (the flex-wrap row has reflowed), so
	// positions there are transient/wrong.
	let _topAnchor: { entityId: string; offset: number } | null = null;
	let _anchorRaf = 0;

	// Content width excluding padding — matches what ResizeObserver's contentRect.width reports.
	// closeDetailPanel/openDetailPanel must use this instead of clientWidth (which includes padding)
	// to avoid predicting one column too many.
	function getContentWidth(): number {
		if (!containerEl) return 0;
		const s = getComputedStyle(containerEl);
		return containerEl.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
	}

	// FLIP helper: capture positions, update columns, animate cards to new positions
	// When instant=true, repack without animation (used for panel open/close)
	async function flipColumns(newCols: number, instant = false) {
		if (newCols === numColumns || !containerEl) return;
		const oldPositions = captureCardPositions();
		numColumns = newCols;
		animateFlip(oldPositions, instant);
	}

	// Scroll-anchor preservation across width-driven re-columns (chat panel
	// open/close, window resize, recent-drawer toggle). The detail panel handles
	// its own scroll-to-card via the panelTransitioning early-return below, so this
	// only runs for the bare observer path — which previously re-columned with no
	// scroll handling, drifting whatever you were looking at off-screen.
	//
	// We anchor on the top-most visible feed item. Each top-level item is a
	// `<div data-entity-id>` keyed by entity_id, so the element survives repacking
	// (Svelte moves it between columns rather than destroying it).
	function captureTopAnchor(): { entityId: string; offset: number } | null {
		if (!containerEl) return null;
		const containerTop = containerEl.getBoundingClientRect().top;
		let best: { entityId: string; offset: number } | null = null;
		for (const el of containerEl.querySelectorAll<HTMLElement>('[data-entity-id]')) {
			const id = el.dataset.entityId;
			if (!id) continue;
			// Offset of this item relative to the scroll viewport's top edge.
			const offset = el.getBoundingClientRect().top - containerTop;
			// The item straddling / nearest the top fold is the user's visual anchor.
			if (!best || Math.abs(offset) < Math.abs(best.offset)) best = { entityId: id, offset };
		}
		return best;
	}

	function restoreTopAnchor(anchor: { entityId: string; offset: number }) {
		if (!containerEl) return;
		const el = containerEl.querySelector<HTMLElement>(
			`[data-entity-id="${CSS.escape(anchor.entityId)}"]`
		);
		if (!el) return;
		const newOffset = el.getBoundingClientRect().top - containerEl.getBoundingClientRect().top;
		// Nudge the scroll so the anchor item returns to the same viewport offset.
		containerEl.scrollTop += newOffset - anchor.offset;
	}

	// Refresh the stored anchor from the current (stable) layout, throttled to one
	// read per frame. Skipped during panel transitions (the detail panel owns scroll
	// then). getBoundingClientRect-based scan is overlay-agnostic, so it stays
	// accurate even while the chat scrim covers the feed.
	function recordTopAnchor() {
		if (_anchorRaf) return;
		_anchorRaf = requestAnimationFrame(() => {
			_anchorRaf = 0;
			if (!panelTransitioning) _topAnchor = captureTopAnchor();
		});
	}

	$effect(() => {
		if (!containerEl) return;
		const el = containerEl;
		el.addEventListener('scroll', recordTopAnchor, { passive: true });
		const observer = new ResizeObserver(([entry]) => {
			if (panelTransitioning) return;

			const w = entry.contentRect.width;
			const newCols = Math.max(1, Math.floor((w + COL_GAP) / (CARD_WIDTH + COL_GAP)));
			_resizeInstant = false;
			if (newCols === numColumns) return; // width changed but layout won't move

			// Restore the anchor captured from the pre-reflow stable layout (not measured
			// here — see _topAnchor). Repack instantly so the restore reads final positions
			// on the next frame rather than mid-FLIP transforms. Fall back to an in-place
			// scan when no anchor is tracked yet (only at the very top, where it's accurate).
			const anchor = _topAnchor ?? captureTopAnchor();
			flipColumns(newCols, true);
			if (anchor) requestAnimationFrame(() => restoreTopAnchor(anchor));
		});
		observer.observe(el);
		return () => {
			el.removeEventListener('scroll', recordTopAnchor);
			if (_anchorRaf) cancelAnimationFrame(_anchorRaf);
			_anchorRaf = 0;
			observer.disconnect();
		};
	});

	// Pre-calculate final column count when detail panel opens/closes
	// so cards reflow once to the final layout before the panel animates
	// skipFlip=true when opening because a card was clicked (scroll will follow, so skip jarring FLIP)
	// skipFlip=false (default) when user manually opens via chevron (normal FLIP animation)
	function openDetailPanel(skipFlip = false) {
		// Panel already open: don't re-run the panel-slide / column repack, but DO
		// scroll the newly-selected card into view (otherwise clicking a second card
		// while the panel is open leaves the selection off-screen).
		if (detailPanelOpen || !containerEl) {
			detailPanelOpen = true;
			if (skipFlip && containerEl) {
				const cardId = selectedCard?.card_id;
				const entityId = lastDetailEntityId;
				requestAnimationFrame(() => {
					if (cardId) scrollToCard(cardId);
					else if (entityId) scrollToGroupElement(entityId);
				});
			}
			return;
		}
		// Panel will take DETAIL_PANEL_WIDTH + COL_GAP from the container
		const currentWidth = getContentWidth();
		const finalWidth = currentWidth - DETAIL_PANEL_WIDTH - COL_GAP;
		const finalCols = Math.max(1, Math.floor((finalWidth + COL_GAP) / (CARD_WIDTH + COL_GAP)));

		panelTransitioning = true;
		flipColumns(finalCols, skipFlip);
		detailPanelOpen = true;

		// Snapshot what to scroll to BEFORE the cleanup runs — selectedCard /
		// lastDetailEntityId could change if the user clicks another card during
		// the 350ms window.
		const scrollCardId = skipFlip ? selectedCard?.card_id ?? null : null;
		const scrollEntityId = skipFlip && !scrollCardId ? lastDetailEntityId : null;

		// Allow ResizeObserver to resume after panel transition ends (300ms duration).
		// Recalculate columns in case the predicted count was wrong. Instant repack
		// to avoid a visible FLIP animation stacking on top of the panel slide.
		// Scroll-to-card runs AFTER the optional final flipColumns so the smooth
		// scroll target isn't invalidated by a late layout shift.
		setTimeout(() => {
			_resizeInstant = true;
			panelTransitioning = false;
			const w = getContentWidth();
			const correctCols = Math.max(1, Math.floor((w + COL_GAP) / (CARD_WIDTH + COL_GAP)));
			if (correctCols !== numColumns) flipColumns(correctCols, true);
			// rAF lets Svelte commit any column-count change to the DOM before we
			// read positions for the scroll.
			if (scrollCardId || scrollEntityId) {
				requestAnimationFrame(() => {
					if (scrollCardId) scrollToCard(scrollCardId);
					else if (scrollEntityId) scrollToGroupElement(scrollEntityId);
				});
			}
		}, 350);
	}

	// Scroll to a card and show a fading highlight border (used when panel closes).
	// Uses instant scroll so only the highlight animates — smooth scroll gets
	// interrupted by ResizeObserver reflows after the panel transition.
	function scrollToCardWithFade(cardId: string) {
		_flipSettled.then(() => {
			const attempt = (tries: number) => {
				requestAnimationFrame(() => {
					const el = findCardElement(cardId);
					if (el) {
						scrollElToCenter(el, 'instant');
						// Fading orange ring highlight via CSS animation
						el.classList.add('card-highlight-fade');
						el.addEventListener('animationend', () => {
							el.classList.remove('card-highlight-fade');
						}, { once: true });
						_scrollToCardId = null;
					} else if (tries > 0) {
						attempt(tries - 1);
					} else {
						_scrollToCardId = null;
					}
				});
			};
			attempt(15);
		});
	}

	function closeDetailPanel() {
		if (!detailPanelOpen || !containerEl) {
			closeDetail();
			return;
		}
		// Use lastDetailCardId which survives dismiss (X button clears selectedCard but not this)
		const lastCardId = lastDetailCardId;
		// Resolve the group entity_id now, before closeDetail clears state.
		// Column repacking destroys/recreates CardGroup components (resetting expanded=false),
		// so the individual card element won't exist after reflow. We scroll to the group
		// wrapper instead, found by entity_id which is stable across repacks.
		// For group summary selections, lastDetailEntityId is already set directly.
		let lastEntityId: string | null = lastDetailEntityId;
		if (!lastEntityId && lastCardId) {
			const g = groups.find((g) => g.cards.some((c) => c.card_id === lastCardId));
			if (g && g.card_count > 1) lastEntityId = g.entity_id;
		}

		const hasScrollTarget = !!(lastEntityId || lastCardId);

		// Panel will free DETAIL_PANEL_WIDTH + COL_GAP back to the container
		const currentWidth = getContentWidth();
		const finalWidth = currentWidth + DETAIL_PANEL_WIDTH + COL_GAP;
		const finalCols = Math.max(1, Math.floor((finalWidth + COL_GAP) / (CARD_WIDTH + COL_GAP)));

		panelTransitioning = true;
		// Skip FLIP when scroll-back will also run (avoids jarring double transition)
		flipColumns(finalCols, hasScrollTarget);
		closeDetail();

		// Apply the highlight fade immediately after column repack so it starts
		// as soon as the panel begins sliding out, not after the 350ms transition.
		const immediateTargetId = lastEntityId || lastCardId;
		if (immediateTargetId) {
			requestAnimationFrame(() => {
				const el = lastEntityId
					? (document.querySelector(`[data-group-row="${lastEntityId}"]`) ?? document.querySelector(`[data-group-entity="${lastEntityId}"]`))
					: findCardElement(immediateTargetId);
				if (el) {
					el.classList.add('card-highlight-fade');
					el.addEventListener('animationend', () => el.classList.remove('card-highlight-fade'), { once: true });
				}
			});
		}

		// Allow ResizeObserver to resume after panel transition ends (300ms duration).
		// Recalculate columns in case the predicted count was wrong, then scroll to
		// the last-active card/group once layout is fully settled. Column correction is
		// instant (no FLIP animation) so only the final scroll+highlight animates.
		setTimeout(() => {
			_resizeInstant = true;
			panelTransitioning = false;
			const w = getContentWidth();
			const correctCols = Math.max(1, Math.floor((w + COL_GAP) / (CARD_WIDTH + COL_GAP)));
			if (correctCols !== numColumns) flipColumns(correctCols, true);
			// Wait a frame for the DOM to repaint after column repack before
			// reading element positions for scroll.
			const scrollTargetId = lastEntityId || lastCardId;
			if (scrollTargetId) {
				requestAnimationFrame(() => {
					const el = lastEntityId
						? document.querySelector(`[data-group-entity="${lastEntityId}"]`)
						: findCardElement(scrollTargetId);
					if (el) {
						scrollElToCenter(el);
					}
				});
			}
		}, 350);
	}

	const RECENT_DRAWER_WIDTH = 260;

	function toggleRecentDrawer() {
		if (!containerEl) {
			$recentDrawerOpen = !$recentDrawerOpen;
			return;
		}
		const currentWidth = getContentWidth();
		const opening = !$recentDrawerOpen;
		const finalWidth = opening
			? currentWidth - RECENT_DRAWER_WIDTH - COL_GAP
			: currentWidth + RECENT_DRAWER_WIDTH + COL_GAP;
		const finalCols = Math.max(1, Math.floor((finalWidth + COL_GAP) / (CARD_WIDTH + COL_GAP)));

		panelTransitioning = true;
		flipColumns(finalCols, true);
		$recentDrawerOpen = opening;

		setTimeout(() => {
			_resizeInstant = true;
			panelTransitioning = false;
			const w = getContentWidth();
			const correctCols = Math.max(1, Math.floor((w + COL_GAP) / (CARD_WIDTH + COL_GAP)));
			if (correctCols !== numColumns) flipColumns(correctCols, true);
		}, 350);
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

<svelte:window onkeydown={(e) => {
	// Escape collapses the focus-mode detail overlay first (matches the chat
	// sidebar, where Escape steps out of the wide overlay before anything else).
	if (e.key === 'Escape' && $detailExpanded) { detailExpanded.set(false); e.preventDefault(); return; }
	if (e.key === 'Escape' && $feedFilters.showRelated) { clearRelatedFilter(); e.preventDefault(); }
}} />

<div class="flex h-full flex-col">
	<!-- Feed toolbar -->
	<div bind:this={toolbarEl} class="flex flex-nowrap items-center gap-1.5 pb-2">
		<!-- Stats -->
		{#if hasSelection && $feedViewMode === 'list'}
			<div class="flex items-center gap-2">
				<span class="text-laya-secondary font-medium text-laya-orange">{selectionCount} selected</span>
				<span class="text-laya-micro text-surface-600">·</span>
				{#if !allVisibleSelected}
					<button
						class="text-laya-secondary text-surface-400 hover:text-surface-200 transition-colors"
						onclick={() => feedSelection.selectMany(allVisibleCardIds)}
					>
						Select All
					</button>
					<span class="text-laya-micro text-surface-600">·</span>
				{/if}
				<button
					class="text-laya-secondary text-surface-400 hover:text-surface-200 transition-colors"
					onclick={() => feedSelection.deselectAll()}
				>
					Deselect All
				</button>
				<div class="ml-1">
					<BulkActionsDropdown {selectedCards} ondelete={handleDelete} onunlinked={handleBulkUnlinked} />
				</div>
			</div>
		{:else}
			<div class="flex items-center gap-1.5 flex-wrap whitespace-nowrap">
				<span class="text-laya-secondary text-surface-500">{totalGroups} {totalGroups === 1 ? 'group' : 'groups'}</span>
				<span class="text-laya-micro text-surface-600">·</span>
				<span class="text-laya-secondary text-surface-500">{totalCards} cards</span>
				{#if searchActive && filteredTotalCards !== totalCards}
					<span class="inline-flex items-center gap-1 rounded-full bg-laya-orange/10 px-2 py-0.5 text-laya-micro font-medium text-laya-orange">
						<svg class="h-2.5 w-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
						{filteredGroups.length} shown
					</span>
				{/if}
				{#if searchActive && !$feedFilters.showAllDaysSearch && !$feedFilters.showBookmarked && !$feedFilters.showRelated}
					<button
						class="text-laya-micro font-medium text-laya-orange hover:underline"
						onclick={() => { $allDaysSavedDate = $feedDate; $feedFilters = { ...$feedFilters, showAllDaysSearch: true }; }}
					>
						Search all days
					</button>
				{/if}
				{#if agentRunningCount > 0}
					<span class="inline-flex items-center gap-1 rounded-full bg-laya-coral/10 px-2 py-0.5 text-laya-micro font-medium text-laya-coral">
						<span class="h-1.5 w-1.5 rounded-full bg-laya-coral animate-pulse"></span>
						{agentRunningCount} running
					</span>
				{/if}
				{#if failedCount > 0}
					<span class="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-laya-micro font-medium text-red-400">
						<span class="h-1.5 w-1.5 rounded-full bg-red-400"></span>
						{failedCount} failed
					</span>
				{/if}
			</div>
		{/if}

		<div class="flex-1" data-feed-spacer></div>

		{#if feedActionsCollapsed}
			<!-- Overflow menu for narrow toolbar -->
			<div class="feed-overflow-menu relative">
				<button
					onclick={() => (feedActionsMenuOpen = !feedActionsMenuOpen)}
					class="flex items-center gap-1 rounded-lg border px-2 py-1 text-laya-secondary transition-colors
						{hasActiveFilters || $feedFilters.showBookmarked || $recentDrawerOpen || summaryModalOpen
							? 'border-laya-orange/30 bg-laya-orange/10 text-laya-orange'
							: 'border-surface-700 bg-surface-800/60 text-surface-400 hover:text-surface-200 hover:border-surface-600'}"
					aria-label="More actions"
				>
					<svg class="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 24 24">
						<circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/>
					</svg>
				</button>
				{#if feedActionsMenuOpen}
					<div class="absolute right-0 top-full z-[100] mt-1 flex flex-col rounded-lg border border-surface-600 bg-surface-800 py-1 shadow-lg min-w-[160px]">
						<button
							class="flex w-full items-center gap-2 whitespace-nowrap px-4 py-1.5 text-laya-secondary transition-colors hover:bg-surface-700
								{hasActiveFilters ? 'text-laya-orange' : 'text-surface-300'}"
							onclick={(e: MouseEvent) => {
								const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
								filterMenuPos = { top: r.top, left: r.left - 264 };
								filterPopoverOpen = !filterPopoverOpen;
							}}
						>
							<svg class="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
							</svg>
							Filters
							{#if hasActiveFilters}
								<span class="flex h-4 w-4 items-center justify-center rounded-full bg-laya-orange text-laya-micro font-bold text-surface-900">{activeStatusCount + activePriorityCount + activeSpaceCount + ($feedFilters.showArchived ? 1 : 0) + ($feedFilters.hasWorkspace ? 1 : 0) + ($feedFilters.showUnreadOnly ? 1 : 0)}</span>
							{/if}
						</button>
						<button
							class="flex w-full items-center gap-2 whitespace-nowrap px-4 py-1.5 text-laya-secondary transition-colors hover:bg-surface-700
								{$recentDrawerOpen ? 'text-laya-orange' : 'text-surface-300'}"
							onclick={() => { toggleRecentDrawer(); feedActionsMenuOpen = false; }}
						>
							<svg class="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
							</svg>
							Recent
						</button>
						<button
							class="flex w-full items-center gap-2 whitespace-nowrap px-4 py-1.5 text-laya-secondary transition-colors hover:bg-surface-700
								{$feedFilters.showBookmarked ? 'text-laya-orange' : 'text-surface-300'}"
							onclick={() => { $feedFilters.showBookmarked = !$feedFilters.showBookmarked; feedActionsMenuOpen = false; }}
						>
							<svg class="h-3.5 w-3.5 shrink-0" fill={$feedFilters.showBookmarked ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
							</svg>
							Bookmarks
						</button>
						<div class="my-0.5 border-t border-surface-700"></div>
							<button
								class="flex w-full items-center gap-2 whitespace-nowrap px-4 py-1.5 text-laya-secondary transition-colors
									{hasUnread ? 'text-surface-300 hover:bg-surface-700' : 'text-surface-600 cursor-not-allowed'}"
								onclick={() => { handleMarkAllRead(); feedActionsMenuOpen = false; }}
								disabled={markingAllRead || !hasUnread}
							>
								<svg class="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
								</svg>
								Mark all read
							</button>
						<div class="my-0.5 border-t border-surface-700"></div>
						<button
							class="flex w-full items-center gap-2 whitespace-nowrap px-4 py-1.5 text-laya-secondary transition-colors hover:bg-surface-700
								{summaryModalOpen ? 'text-laya-orange' : 'text-surface-300'}"
							onclick={() => { setSummaryModalOpen(true); feedActionsMenuOpen = false; }}
						>
							<svg class="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
							</svg>
							Summary
						</button>
					</div>
				{/if}
			</div>
		{:else}
			<!-- Inline action buttons -->
			<div class="filter-dropdown relative" bind:this={filterBtnEl}>
				<button
					onclick={() => { if (!filterPopoverOpen && filterBtnEl) { const r = filterBtnEl.getBoundingClientRect(); filterMenuPos = { top: r.bottom + 6, left: r.left }; } filterPopoverOpen = !filterPopoverOpen; }}
					class="relative flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-laya-secondary transition-colors
						{hasActiveFilters
							? 'border-laya-orange/30 bg-laya-orange/10 text-laya-orange'
							: 'border-surface-700 bg-surface-800/60 text-surface-400 hover:text-surface-200 hover:border-surface-600'}"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
					</svg>
					Filters
					{#if hasActiveFilters}
						<span class="flex h-4 w-4 items-center justify-center rounded-full bg-laya-orange text-laya-micro font-bold text-surface-900">{activeStatusCount + activePriorityCount + activeSpaceCount + ($feedFilters.showArchived ? 1 : 0) + ($feedFilters.hasWorkspace ? 1 : 0) + ($feedFilters.showUnreadOnly ? 1 : 0)}</span>
					{/if}
				</button>
			</div>

			<button
				onclick={toggleRecentDrawer}
				class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-laya-secondary transition-colors
					{$recentDrawerOpen
						? 'border-laya-orange/30 bg-laya-orange/10 text-laya-orange'
						: 'border-surface-700 bg-surface-800/60 text-surface-400 hover:text-surface-200 hover:border-surface-600'}"
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
				</svg>
				Recent
			</button>

			<button
				onclick={() => ($feedFilters.showBookmarked = !$feedFilters.showBookmarked)}
				class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-laya-secondary transition-colors
					{$feedFilters.showBookmarked
						? 'border-laya-orange/30 bg-laya-orange/10 text-laya-orange'
						: 'border-surface-700 bg-surface-800/60 text-surface-400 hover:text-surface-200 hover:border-surface-600'}"
			>
				<svg class="h-3.5 w-3.5" fill={$feedFilters.showBookmarked ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
				</svg>
				Bookmarks
			</button>

			<div class="h-5 w-px bg-surface-700/60 mx-0.5"></div>

			<button
					onclick={handleMarkAllRead}
					disabled={markingAllRead || !hasUnread}
					class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-laya-secondary transition-colors
						border-surface-700 bg-surface-800/60
						{hasUnread
							? 'text-surface-400 hover:text-surface-200 hover:border-surface-600'
							: 'text-surface-600 cursor-not-allowed'}
						disabled:opacity-40"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
					</svg>
					Mark all read
				</button>

			<div class="h-5 w-px bg-surface-700/60 mx-0.5"></div>

			<button
				onclick={() => { setSummaryModalOpen(true); }}
				class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-laya-secondary transition-colors
					{summaryModalOpen
						? 'border-laya-orange/30 bg-laya-orange/10 text-laya-orange'
						: 'border-surface-700 bg-surface-800/60 text-surface-400 hover:text-surface-200 hover:border-surface-600'}"
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
				</svg>
				Summary
			</button>
		{/if}

		<!-- Filter popover (rendered outside collapse conditional so it works in both modes) -->
		<FilterPopover
			open={filterPopoverOpen}
			pos={filterMenuPos}
			hasActiveFilters={hasActiveFilters}
		/>

		<!-- Separator -->
		<div class="h-5 w-px bg-surface-700/60 mx-1"></div>
		<!-- Search -->
		<div class="group/search relative">
			<label
				class="flex items-center h-7 w-48 rounded-lg border border-surface-700 bg-surface-800/60 transition-colors focus-within:border-laya-orange/50 focus-within:ring-1 focus-within:ring-laya-orange/25"
			>
				<svg class="pointer-events-none ml-2 h-3.5 w-3.5 shrink-0 text-surface-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
				</svg>
				{#if activeSearchTags.length > 0}
					<div class="flex items-center gap-0.5 ml-1 shrink-0">
						{#each activeSearchTags.slice(0, 4) as tagName}
							{@const tag = availableTags.find(t => t.name === tagName)}
							<span class="h-2 w-2 rounded-full shrink-0" style="background-color: {tag?.color ?? (tag?.is_system ? '#6B7280' : '#C4956B')}"></span>
						{/each}
						{#if activeSearchTags.length > 4}
							<span class="text-[8px] leading-none text-surface-500 shrink-0">+{activeSearchTags.length - 4}</span>
						{/if}
					</div>
				{/if}
				<input
					bind:this={searchInputEl}
					type="text"
					bind:value={searchQuery}
					placeholder={activeSearchTags.length > 0 ? 'Search...' : 'Search or #tag'}
					class="h-full flex-1 min-w-0 bg-transparent pl-1.5 pr-7 text-laya-secondary text-surface-200 placeholder-surface-500 outline-none"
					oninput={() => updateTagAutocomplete()}
					onblur={() => { setTimeout(() => { showTagAutocomplete = false; }, 150); }}
					onkeydown={(e) => {
						if (!showTagAutocomplete || tagSuggestions.length === 0) return;
						if (e.key === 'ArrowDown') {
							e.preventDefault();
							tagAutocompleteIdx = (tagAutocompleteIdx + 1) % tagSuggestions.length;
						} else if (e.key === 'ArrowUp') {
							e.preventDefault();
							tagAutocompleteIdx = (tagAutocompleteIdx - 1 + tagSuggestions.length) % tagSuggestions.length;
						} else if (e.key === 'Enter' || e.key === 'Tab') {
							e.preventDefault();
							insertTagToken(tagSuggestions[tagAutocompleteIdx].name);
						} else if (e.key === 'Escape') {
							showTagAutocomplete = false;
						}
					}}
				/>
				{#if searchQuery}
					<button
						class="mr-1.5 rounded p-0.5 text-surface-500 hover:text-surface-300 shrink-0"
						onclick={() => { searchQuery = ''; showTagAutocomplete = false; }}
						title="Clear search"
					>
						<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
						</svg>
					</button>
				{/if}
			</label>
			{#if activeSearchTags.length > 0}
				<div class="absolute left-0 top-full z-50 hidden group-hover/search:block pt-1">
					<div class="rounded-lg border border-surface-700 bg-surface-900 p-2 shadow-xl min-w-[140px]">
						<div class="flex flex-col gap-1.5">
							{#each activeSearchTags as tagName}
								{@const tag = availableTags.find(t => t.name === tagName)}
								<div class="flex items-center justify-between gap-3">
									<span
										class="{tag?.is_system ? 'tag-chip-system' : 'tag-chip-user'} inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium leading-none"
										style="--tag-color: {tag?.color ?? (tag?.is_system ? '#6B7280' : '#C4956B')}"
									>#{tagName}</span>
									<button
										class="rounded p-0.5 text-surface-500 hover:text-red-400 transition-colors cursor-pointer"
										title="Remove #{tagName}"
										onclick={() => removeSearchTag(tagName)}
									>
										<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
											<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
										</svg>
									</button>
								</div>
							{/each}
						</div>
						<button
							class="mt-2 w-full rounded-md py-1 text-[10px] font-medium text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors cursor-pointer"
							onclick={() => { activeSearchTags.forEach(t => removeSearchTag(t)); }}
						>Clear all</button>
					</div>
				</div>
			{/if}
		</div>
		<!-- View toggle -->
		<div class="flex items-center rounded-lg border border-surface-700 bg-surface-800/60 p-0.5">
			<div class="group/tip relative">
				<button
					class="flex items-center gap-1 rounded-md px-2 py-1 text-laya-secondary transition-colors {$feedViewMode === 'card' ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-400 hover:text-surface-200'}"
					onclick={() => ($feedViewMode = 'card')}
					aria-label="Card View"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
					</svg>
				</button>
				<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-laya-micro font-medium opacity-0 transition-opacity duration-75 group-hover/tip:opacity-100">Card View</span>
			</div>
			<div class="group/tip relative">
				<button
					class="flex items-center gap-1 rounded-md px-2 py-1 text-laya-secondary transition-colors {$feedViewMode === 'list' ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-400 hover:text-surface-200'}"
					onclick={() => ($feedViewMode = 'list')}
					aria-label="List View"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
					</svg>
				</button>
				<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-laya-micro font-medium opacity-0 transition-opacity duration-75 group-hover/tip:opacity-100">List View</span>
			</div>
		</div>
	</div>

	<!-- Content area: recent drawer + cards + detail panel side by side -->
	<div class="flex min-h-0 flex-1 gap-4" class:panel-transitioning={panelTransitioning}>
		<!-- Recent Cards drawer -->
		<RecentDrawer
			cards={filteredRecentCards}
			spaceFiltered={$feedFilters.spaceFilter.length > 0}
			selectedCardId={selectedCard?.card_id ?? null}
			selectedGroupEntityId={selectedGroupSummary?.group.entity_id ?? null}
			onNavigate={handleRecentCardClick}
			onToggle={toggleRecentDrawer}
		/>
		<!-- Cards / Summary / List section -->
		<!-- data-view-mode: scopes the panel-transitioning container-type toggle in app.css
		     to list view only — card view must never have container-type flipped mid-slide,
		     it causes WKWebView to skip painting descendants. -->
		<div bind:this={containerEl} data-view-mode={$feedViewMode} class="feed-list-container flex min-w-0 flex-1 flex-col overflow-y-auto p-3 transition-opacity duration-[250ms] ease-out {relatedViewExiting ? 'opacity-0' : 'opacity-100'}">
			{#if $feedFilters.showRelated}
				<div class="mb-3 flex items-center gap-2 rounded-lg border border-laya-orange/30 bg-laya-orange/10 px-3 py-2">
					<svg class="h-4 w-4 shrink-0 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
					</svg>
					<span class="min-w-0 flex-1 truncate text-laya-secondary text-laya-orange">
						Related to "<span class="font-medium">{$feedFilters.relatedSourceHeader}</span>"
					</span>
					<button
						onclick={() => clearRelatedFilter()}
						class="shrink-0 rounded p-0.5 text-laya-orange/70 transition-colors hover:bg-laya-orange/20 hover:text-laya-orange"
						aria-label="Clear related cards filter"
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
						</svg>
					</button>
				</div>
			{/if}
			{#if loading && groups.length === 0}
				<div class="py-12 text-center text-surface-400">Loading cards...</div>
			{:else if error}
				<div class="flex items-start gap-2 rounded-lg border border-red-800 bg-red-900/30 px-4 py-3 text-laya-base text-red-300">
					<span class="flex-1">{error}</span>
					<button class="shrink-0 text-red-400 hover:text-red-200" onclick={() => (error = null)} aria-label="Dismiss error">
						<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
					</button>
				</div>
			{:else if groups.length === 0}
				<div class="py-12 text-center text-surface-500">
					<p class="text-laya-heading">{$feedFilters.showRelated ? 'No related cards found' : $feedFilters.showBookmarked ? 'No bookmarked cards' : `No cards for ${formatDateLabel($feedDate)}`}</p>
					<p class="mt-1 text-laya-base">
						{#if $feedFilters.showRelated}
							<button class="text-laya-orange hover:underline" onclick={() => clearRelatedFilter()}>Back to feed</button>
						{:else if $feedFilters.showBookmarked}
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
			{:else if filteredGroups.length === 0 && searchActive}
				<div class="py-12 text-center text-surface-500">
					<svg class="mx-auto mb-2 h-8 w-8 text-surface-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
					</svg>
					<p class="text-laya-base">No cards match "<span class="text-surface-300">{searchQuery}</span>"</p>
					<button class="mt-2 text-laya-secondary text-laya-orange hover:underline" onclick={() => (searchQuery = '')}>Clear search</button>
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
							<span class="text-laya-secondary font-semibold uppercase tracking-wider text-surface-400">{sectionTitle}</span>
							<div class="flex-1 border-t border-surface-700"></div>
							<span class="text-laya-micro text-surface-500">{sectionGroups.reduce((s, g) => s + g.card_count, 0)}</span>
						</div>
						{#if !isCollapsed}
							<div class="flex flex-col gap-1 mb-2">
								{#each sectionGroups as group (group.entity_id)}
									<div data-entity-id={group.entity_id} data-list-row>
									{#if group.card_count === 1}
										<ListRow card={group.cards[0]} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} bulkSelected={$feedSelection.has(group.cards[0].card_id)} onbulktoggle={handleBulkToggle} hasSelection={hasAnySelection} lastViewedCardId={lastViewedCardId ?? ''} />
									{:else}
										<ListGroupComponent {group} onselect={selectCard} onselectgroup={selectGroupSummary} ondelete={handleDelete} onlink={handleLinkGroup} selectedCardId={selectedCard?.card_id ?? ''} {selectedEntityId} scrollToCardId={_scrollToCardId} bulkSelectedIds={$feedSelection} onbulktoggle={handleBulkToggle} onbulktogglegroup={handleBulkToggleGroup} hasSelection={hasAnySelection} lastViewedCardId={lastViewedCardId ?? ''} lastViewedEntityId={lastViewedEntityId ?? ''} />
									{/if}
									</div>
								{/each}
							</div>
						{/if}
					{/each}
				{:else}
					<!-- Default list view -->
					<div class="flex flex-col gap-1">
						{#each filteredGroups as group (group.entity_id)}
							<div data-entity-id={group.entity_id} data-list-row>
							{#if group.card_count === 1}
								<ListRow card={group.cards[0]} onselect={selectCard} ondelete={handleDelete} selectedCardId={selectedCard?.card_id ?? ''} bulkSelected={$feedSelection.has(group.cards[0].card_id)} onbulktoggle={handleBulkToggle} hasSelection={hasAnySelection} lastViewedCardId={lastViewedCardId ?? ''} />
							{:else}
								<ListGroupComponent {group} onselect={selectCard} onselectgroup={selectGroupSummary} ondelete={handleDelete} onlink={handleLinkGroup} selectedCardId={selectedCard?.card_id ?? ''} {selectedEntityId} scrollToCardId={_scrollToCardId} bulkSelectedIds={$feedSelection} onbulktoggle={handleBulkToggle} onbulktogglegroup={handleBulkToggleGroup} hasSelection={hasAnySelection} lastViewedCardId={lastViewedCardId ?? ''} lastViewedEntityId={lastViewedEntityId ?? ''} />
							{/if}
							</div>
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
						<span class="text-laya-secondary font-semibold uppercase tracking-wider text-surface-400">{sectionTitle}</span>
						<div class="flex-1 border-t border-surface-700"></div>
						<span class="text-laya-micro text-surface-500">{sectionGroups.reduce((s, g) => s + g.card_count, 0)}</span>
					</div>
					{#if !isCollapsed}
						<div class="flex flex-wrap gap-4">
							{#each toColumns(sectionGroups) as col}
								<div class="flex w-[320px] flex-col gap-4">
									{#each col as group (group.entity_id)}
									{@const isGroupExiting = group.cards.every((c) => exitingCardIds.has(c.card_id))}
									<div data-entity-id={group.entity_id} class="card-exit-wrap {isGroupExiting ? 'card-exiting' : ''}">
										{#if group.card_count === 1}
											<ActionCardComponent card={group.cards[0]} onselect={selectCard} ondelete={handleDelete} onlink={handleLinkCard} selectedCardId={selectedCard?.card_id ?? ''} hasSelection={hasAnySelection} lastViewedCardId={lastViewedCardId ?? ''} />
										{:else}
											<CardGroupComponent {group} onselect={selectCard} onselectgroup={selectGroupSummary} ondelete={handleDelete} onlink={handleLinkGroup} selectedCardId={selectedCard?.card_id ?? ''} {selectedEntityId} hasSelection={hasAnySelection} lastViewedCardId={lastViewedCardId ?? ''} lastViewedEntityId={lastViewedEntityId ?? ''} scrollToCardId={_scrollToCardId} {detailPanelOpen} />
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
										<ActionCardComponent card={group.cards[0]} onselect={selectCard} ondelete={handleDelete} onlink={handleLinkCard} selectedCardId={selectedCard?.card_id ?? ''} hasSelection={hasAnySelection} lastViewedCardId={lastViewedCardId ?? ''} />
									{:else}
										<CardGroupComponent {group} onselect={selectCard} onselectgroup={selectGroupSummary} ondelete={handleDelete} onlink={handleLinkGroup} selectedCardId={selectedCard?.card_id ?? ''} {selectedEntityId} hasSelection={hasAnySelection} lastViewedCardId={lastViewedCardId ?? ''} lastViewedEntityId={lastViewedEntityId ?? ''} scrollToCardId={_scrollToCardId} {detailPanelOpen} />
									{/if}
								</div>
							{/each}
						</div>
					{/each}
				</div>
			{/if}
			{#if hasMoreGroups}
				<!-- Group pagination: load the next page of groups (P4-9). Sits below
				     both list and card views; hidden when the server has no more. -->
				<div class="flex w-full justify-center py-6">
					<button
						class="rounded-lg border border-surface-600 bg-surface-800 px-6 py-2.5 text-laya-base font-medium text-surface-200 transition-colors hover:border-laya-orange/40 hover:bg-surface-700 disabled:cursor-not-allowed disabled:opacity-60"
						onclick={loadMoreGroups}
						disabled={loadingMoreGroups}
					>
						{loadingMoreGroups ? 'Loading…' : `Load more (${totalGroups - groups.length} more)`}
					</button>
				</div>
			{/if}
		</div>

		<!-- Detail panel -->
		<div bind:this={detailSlotEl} class="relative flex flex-shrink-0">
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

			<!-- Sliding panel (inline layout). In focus mode ($detailExpanded) the body
			     renders in the fixed overlay below instead — but this slot keeps its width
			     so the feed cards don't reflow when the user expands/collapses. -->
			<div
				class="overflow-hidden transition-[width] duration-300 ease-in-out {detailPanelOpen ? 'w-[420px]' : 'w-0'}"
			>
				<div class="w-[420px] h-full overflow-y-auto">
					{#if !$detailExpanded}
						{@render detailBody()}
					{/if}
				</div>
			</div>
		</div>
	</div>
</div>

<!-- Shared description-panel body, rendered either in the inline slot above or in
     the focus-mode overlay below (never both at once). -->
{#snippet detailBody()}
	{#if selectedGroupSummary}
		<GroupSummaryDetail
			summary={selectedGroupSummary.summary}
			group={selectedGroupSummary.group}
			generating={generatingEntityIds.has(selectedGroupSummary.group.entity_id)}
			onclose={closeDetailPanel}
			ondismiss={dismissActiveGroupSummary}
			onshowcards={() => {
				const entityId = selectedGroupSummary?.group.entity_id;
				if (entityId) {
					const el = document.querySelector(`[data-group-entity="${entityId}"]`);
					if (el) el.dispatchEvent(new CustomEvent('expand'));
				}
			}}
			ongotogroup={(entityId) => scrollToGroupElement(entityId)}
			ongenerate={(entityId) => {
				generatingEntityIds.add(entityId);
				generatingEntityIds = new Set(generatingEntityIds);
			}}
			onshowrelated={handleShowRelated}
			onrunagent={handleRunEntityAgent}
		/>
	{:else if selectedCard}
		<CardDetail
			card={selectedCard}
			onclose={closeDetailPanel}
			ondismiss={dismissActiveCard}
			ongotocard={gotoCard}
			onlink={handleLinkCard}
			onshowrelated={handleShowRelated}
			onunlinked={handleUnlinked}
			onrunagent={selectedCardIsSingleEntity ? handleRunEntityAgent : undefined}
		/>
	{:else}
		<div class="flex h-full flex-col items-center justify-center rounded-xl border border-dashed border-surface-700 text-surface-600">
			<svg class="mb-2 h-8 w-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
			</svg>
			<p class="text-laya-secondary">Select a card to view details</p>
		</div>
	{/if}
{/snippet}

<!-- Focus-mode ("expand") overlay for the description panel — identical mechanism
     to the chat sidebar's focus mode (see ChatSidebar.svelte): a widened panel that
     floats over the feed behind a dim scrim instead of pushing the cards aside. The
     inline slot above stays reserved so the cards don't shift when toggling. -->
{#if hasAnySelection && $detailExpanded && overlayGeom}
	<!-- Dim scrim behind the overlay; click to collapse. Starts at the panel's top
	     (so the toolbar above stays bright) but extends full-width to the VIEWPORT
	     bottom — covering main's 16px padding gutter (right) and the padding+footer
	     strip (bottom) so no feed-page background peeks around the floating panel.
	     z-30 keeps it below the panel (z-40). -->
	<button
		aria-label="Collapse detail panel"
		onclick={() => detailExpanded.set(false)}
		class="fixed inset-x-0 bottom-0 z-30 cursor-default chat-scrim backdrop-blur-sm"
		style="top: {overlayGeom.top}px;"
		transition:fade={{ duration: $reducedMotion ? 0 : 200 }}
	></button>
	<!-- The overlay is pinned to the inline panel's exact box (top/bottom/right) and
	     only its width grows leftward over the cards, so the focus-mode panel keeps the
	     same height as the non-focus panel — it reads as the inline panel widening. -->
	<aside
		class="fixed z-40 flex flex-col"
		style="top: {overlayGeom.top}px; bottom: {overlayGeom.bottom}px; right: {overlayGeom.right}px; width: {overlayGeom.width}px;"
		transition:panelGrow={{ start: DETAIL_PANEL_WIDTH, full: overlayGeom.width, duration: $reducedMotion ? 0 : 240 }}
	>
		<!-- When expanded the detail components drop their own card chrome, so this
		     surface reads as one panel matching the inline panel's rounded box. -->
		<div class="flex flex-1 flex-col overflow-hidden rounded-xl shadow-2xl {$glassTheme ? 'glass-section chat-overlay-surface' : 'border border-surface-700 bg-surface-900'}">
			{@render detailBody()}
		</div>
	</aside>
{/if}

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
					<h3 class="text-laya-base font-semibold text-surface-100">Set up your integrations</h3>
					<p class="mt-1.5 text-laya-secondary leading-relaxed text-surface-400">
						Connect your tools to start receiving cards. Set up Gmail, Jira, Slack, GitHub, and more from the Integrations settings.
					</p>
				</div>
			</div>

			<div class="mt-5 flex items-center gap-2">
				<a
					href="/settings?tab=integrations"
					onclick={() => showIntegrationsPopup = false}
					class="inline-flex items-center gap-1.5 rounded-md bg-laya-orange px-4 py-2 text-laya-secondary font-medium text-surface-900 transition-colors hover:bg-laya-gold"
				>
					<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
					</svg>
					Open Integrations
				</a>
				<button
					onclick={() => showIntegrationsPopup = false}
					class="rounded-md px-4 py-2 text-laya-secondary text-surface-400 transition-colors hover:text-surface-200"
				>
					Later
				</button>
			</div>
		</div>
	</div>
{/if}

{#if linkSourceGroup}
	<LinkDialog
		sourceGroup={linkSourceGroup}
		allGroups={groups}
		onclose={() => linkSourceGroup = null}
	/>
{/if}

<!-- Summary modal -->
<SummaryModal
	open={summaryModalOpen}
	summary={filteredDaySummary}
	loading={summaryLoading}
	updatedAt={summaryUpdatedAt}
	dateLabel={formatDateLabel($feedDate)}
	spaceFilter={$feedFilters.spaceFilter}
	onClose={() => setSummaryModalOpen(false)}
	onGotoCard={handleSummaryGotoCard}
/>

{#if showTagAutocomplete && tagSuggestions.length > 0}
	<div
		use:portal
		class="fixed z-[100] w-48 rounded-lg border p-1 {$glassTheme ? 'glass-menu' : 'border-surface-600 bg-surface-900 shadow-xl shadow-black/50'}"
		style="top: {tagAutocompletePos.top}px; left: {tagAutocompletePos.left}px;"
		role="listbox"
	>
		{#each tagSuggestions as tag, idx}
			<button
				class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs transition-colors cursor-pointer
					{idx === tagAutocompleteIdx ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-300 hover:bg-surface-700/50'}"
				role="option"
				aria-selected={idx === tagAutocompleteIdx}
				onmousedown={(e) => { e.preventDefault(); insertTagToken(tag.name); }}
				onmouseenter={() => { tagAutocompleteIdx = idx; }}
			>
				{#if tag.color}
					<span class="h-2 w-2 rounded-full shrink-0" style="background-color: {tag.color}"></span>
				{/if}
				{tag.name}
			</button>
		{/each}
	</div>
{/if}
