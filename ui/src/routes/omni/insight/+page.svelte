<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { untrack } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import type {
		ActionCard,
		OmniBucket,
		OmniEvidenceCard,
		OmniItem,
		OmniItemResponse,
		OmniLineage,
		SuggestedAction
	} from '$lib/api/types';
	import { lastMessage } from '$lib/stores/websocket';
	import {
		chatOpen,
		chatCardContext,
		chatCardIds,
		chatListOpen,
		chatInputPreset,
		pendingCardId
	} from '$lib/stores/chat';
	import { cardSize } from '$lib/stores/cardSize';
	import { buildCardContext } from '$lib/utils/cardContext';
	import { cardBucket } from '$lib/omni/buckets';
	import { layerLabel } from '$lib/omni/layers';
	import type { EvidenceActionContext } from '$lib/omni/evidenceActions';
	import {
		omniExpandedCards,
		omniItemFilter,
		omniShowAllEvidence,
		resetItemView
	} from '$lib/stores/omniView';
	import ClaimHeader from '$lib/components/omni/item/ClaimHeader.svelte';
	import ClaimBreakdown from '$lib/components/omni/item/ClaimBreakdown.svelte';
	import EvidenceList from '$lib/components/omni/item/EvidenceList.svelte';
	import ItemContextRail from '$lib/components/omni/item/ItemContextRail.svelte';

	// --- Navigation contract (B.7) ---
	// New links carry the item's identity: ?v=&section=&item=&space_id=
	// Old links carry only ?cards= — still honoured, in a degraded form with no
	// claim, no lineage and no provenance, because that's all they can express.
	const itemKey = $derived($page.url.searchParams.get('item'));
	const sectionParam = $derived($page.url.searchParams.get('section'));
	const spaceId = $derived($page.url.searchParams.get('space_id') ?? 'default');
	const versionParam = $derived(Number($page.url.searchParams.get('v')) || undefined);
	const legacyCardIds = $derived($page.url.searchParams.getAll('cards'));

	let item = $state<OmniItem | null>(null);
	let section = $state<string | null>(null);
	let version = $state<number | null>(null);
	let cards = $state<OmniEvidenceCard[]>([]);
	let missingCardIds = $state<string[]>([]);
	let lineage = $state<OmniLineage | null>(null);
	let shareOfDay = $state<OmniItemResponse['share_of_day'] | null>(null);
	let sourceCardCount = $state(0);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let showMissing = $state(false);

	// --- Action state (preserved verbatim from the previous insight page) ---
	let executingActionId = $state<string | null>(null);
	let executeError = $state<string | null>(null);
	let editingActionId = $state<string | null>(null);
	let editedPayload = $state<Record<string, string>>({});
	let savingPayload = $state(false);
	let polishingActionIds = $state(new Set<string>());
	let polishErrors = $state<Record<string, string>>({});
	const _polishSeededIds = new Set<string>();

	let evidenceList = $state<ReturnType<typeof EvidenceList> | null>(null);

	const filtered = $derived(
		$omniItemFilter ? cards.filter((c) => cardBucket(c) === $omniItemFilter) : cards
	);

	// Keyed on the query string, not onMount: the layout re-keys on *pathname*, so
	// moving between two /omni/insight URLs (Back/Forward, or a link from one
	// item to another) reuses this component and would otherwise never reload.
	let loadedFor = '';
	$effect(() => {
		const search = $page.url.search;
		if (search === loadedFor) return;
		loadedFor = search;
		untrack(() => load());
	});

	async function load() {
		loading = true;
		error = null;
		resetItemView();
		try {
			if (itemKey) {
				const resp = await engineApi.getOmniItem({
					spaceId,
					version: versionParam,
					section: sectionParam ?? undefined,
					itemKey
				});
				item = resp.item;
				section = resp.section;
				version = resp.version;
				cards = resp.cards;
				missingCardIds = resp.missing_card_ids;
				lineage = resp.lineage;
				shareOfDay = resp.share_of_day;
				sourceCardCount = resp.item.source_cards.length;
			} else if (legacyCardIds.length > 0) {
				await loadLegacy();
			} else {
				error = 'This link carries no Omni item.';
			}
			autoExpandFirst();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load this Omni item';
		} finally {
			loading = false;
		}
	}

	/**
	 * Degraded path for pre-redesign links. Fetches per card as before, but no
	 * longer drops rejections on the floor — the ids that failed become
	 * `missingCardIds` so the rail can say what's missing.
	 */
	async function loadLegacy() {
		const results = await Promise.allSettled(legacyCardIds.map((id) => engineApi.getCard(id)));
		const loaded: OmniEvidenceCard[] = [];
		const missing: string[] = [];
		results.forEach((r, i) => {
			if (r.status === 'fulfilled') {
				loaded.push({
					...r.value,
					bucket: cardBucket(r.value),
					platform: r.value.entity_id?.split(':')[0] ?? 'unknown'
				});
			} else {
				missing.push(legacyCardIds[i]);
			}
		});
		cards = loaded;
		missingCardIds = missing;
		sourceCardCount = legacyCardIds.length;
		if (loaded.length === 0) error = 'None of the referenced cards could be found';
	}

	/** The first row of the active filter opens by default. */
	function autoExpandFirst() {
		omniExpandedCards.set(new Set(filtered[0] ? [filtered[0].card_id] : []));
	}

	function toggleCard(cardId: string) {
		omniExpandedCards.update((current) => {
			const next = new Set(current);
			if (!next.delete(cardId)) next.add(cardId);
			return next;
		});
	}

	function expandCards(cardIds: string[]) {
		omniExpandedCards.set(new Set(cardIds));
	}

	function setFilter(bucket: OmniBucket | null) {
		omniItemFilter.set(bucket);
		omniShowAllEvidence.set(false);
		// Expand the first row of the new filter, and put the list back at the top
		// by setting scrollTop directly — scrollIntoView would move the whole page.
		const next = bucket ? cards.filter((c) => cardBucket(c) === bucket) : cards;
		omniExpandedCards.set(new Set(next[0] ? [next[0].card_id] : []));
		evidenceList?.scrollToTop();
	}

	/** Focus a single card (from the claim breakdown) — everything else closes. */
	function selectCard(cardId: string) {
		omniExpandedCards.set(new Set([cardId]));
	}

	// --- Chat ---

	function chatContextIds(): string[] {
		return cards.map((c) => c.card_id);
	}

	function openCardChat(prompt?: string) {
		chatCardContext.set(buildCardContext(cards as ActionCard[]));
		chatCardIds.set(chatContextIds());
		if (prompt) chatInputPreset.set(prompt);
		chatListOpen.set(false);
		chatOpen.set(true);
	}

	// --- Navigation ---

	function goBack() {
		// Back lands on the board AT the version this claim came from, so the user
		// returns to the same reading rather than being jumped to the present.
		goto(version ? `/omni?v=${version}` : '/omni');
	}

	function showInPulse(cardId: string) {
		pendingCardId.set(cardId);
		goto('/feed');
	}

	// --- Action handlers (unchanged behaviour) ---

	function startEditing(action: SuggestedAction, fallbackBody?: string) {
		editingActionId = action.action_id;
		const p = action.payload as Record<string, unknown>;
		editedPayload = {};
		for (const [key, value] of Object.entries(p)) {
			if (typeof value === 'string' && value.length > 0) editedPayload[key] = value;
		}
		// When the engine couldn't parse the LLM's payload the action has no
		// body/comment field but the draft still lives in staged_output.content.
		// Seed `body` so the user can still edit + save.
		if (
			fallbackBody &&
			!editedPayload.body &&
			!editedPayload.comment &&
			!editedPayload.message &&
			!editedPayload.description
		) {
			editedPayload.body = fallbackBody;
		}
	}

	function cancelEditing() {
		editingActionId = null;
		editedPayload = {};
	}

	async function savePayload(card: ActionCard, action: SuggestedAction) {
		savingPayload = true;
		try {
			await engineApi.updateActionPayload(card.card_id, action.action_id, editedPayload);
			Object.assign(action.payload, editedPayload);
			// Flip `_edited` locally so Polish appears immediately; the WS echo confirms.
			action.payload._edited = true;
			editingActionId = null;
			editedPayload = {};
		} catch (err) {
			executeError = err instanceof Error ? err.message : 'Failed to save draft';
		} finally {
			savingPayload = false;
		}
	}

	async function polishDraft(card: ActionCard, action: SuggestedAction) {
		if (polishingActionIds.has(action.action_id)) return;
		// Optimistic spinner — confirmed by the WS echo once the server flips
		// `_polishing` to true, then cleared when polish completes.
		polishingActionIds = new Set([...polishingActionIds, action.action_id]);
		const { [action.action_id]: _drop, ...restErrors } = polishErrors;
		polishErrors = restErrors;
		try {
			await engineApi.polishActionPayload(card.card_id, action.action_id);
		} catch (err) {
			const next = new Set(polishingActionIds);
			next.delete(action.action_id);
			polishingActionIds = next;
			polishErrors = {
				...polishErrors,
				[action.action_id]: err instanceof Error ? err.message : 'Polish failed'
			};
		}
	}

	async function executeAction(card: ActionCard, actionId: string) {
		executingActionId = actionId;
		executeError = null;
		try {
			const mods =
				editingActionId === actionId && Object.keys(editedPayload).length > 0
					? editedPayload
					: undefined;
			const result = await engineApi.executeAction(card.card_id, actionId, mods);
			card.status = result.status as ActionCard['status'];
			card.selected_action_id = actionId;
			editingActionId = null;
			editedPayload = {};
		} catch (err) {
			executeError = err instanceof Error ? err.message : 'Execution failed';
		} finally {
			executingActionId = null;
		}
	}

	// Seed polish state from persisted `_polishing` flags whenever cards load.
	// Per-action seeding so client-side errors survive later payload mutations.
	$effect(() => {
		for (const card of cards) {
			for (const a of card.suggested_actions ?? []) {
				if (_polishSeededIds.has(a.action_id)) continue;
				_polishSeededIds.add(a.action_id);
				const p = a.payload as Record<string, unknown> | undefined;
				if (p?._polishing === true) {
					polishingActionIds = new Set([...polishingActionIds, a.action_id]);
				}
				if (typeof p?._polish_error === 'string') {
					polishErrors = { ...polishErrors, [a.action_id]: p._polish_error as string };
				}
			}
		}
	});

	// React to per-action payload updates streamed over the WebSocket. The body
	// runs in untrack() so the effect depends only on $lastMessage — without it,
	// reading card.suggested_actions tracks the reactive proxies and re-triggers
	// the same effect on every payload write, freezing the UI.
	$effect(() => {
		const msg = $lastMessage;
		if (!msg || msg.type !== 'action_payload_updated') return;
		untrack(() => {
			const cardId = (msg as { card_id?: string }).card_id;
			const card = cards.find((c) => c.card_id === cardId);
			if (!card) return;
			const actionId = (msg as { action_id?: string }).action_id;
			const newPayload = (msg.payload as { payload?: Record<string, unknown> })?.payload;
			if (!actionId || !newPayload) return;
			const action = card.suggested_actions?.find((a) => a.action_id === actionId);
			if (action) Object.assign(action.payload, newPayload);
			if (newPayload._polishing === true) {
				polishingActionIds = new Set([...polishingActionIds, actionId]);
			} else if (newPayload._polishing === false) {
				const next = new Set(polishingActionIds);
				next.delete(actionId);
				polishingActionIds = next;
			}
			const err = newPayload._polish_error;
			if (typeof err === 'string' && err) {
				polishErrors = { ...polishErrors, [actionId]: err };
			} else if (newPayload._polishing === false && actionId in polishErrors) {
				const { [actionId]: _drop, ...rest } = polishErrors;
				polishErrors = rest;
			}
		});
	});

	// A stable object with getters rather than a $derived literal. The rows
	// `bind:` into `editedPayload`, and a derived would hand them a fresh copy
	// each recompute — the getter returns the page's own $state proxy, so the
	// textarea writes land on the state the save/execute handlers read.
	const actionContext: EvidenceActionContext = {
		get executingActionId() {
			return executingActionId;
		},
		get editingActionId() {
			return editingActionId;
		},
		get editedPayload() {
			return editedPayload;
		},
		get savingPayload() {
			return savingPayload;
		},
		get polishingActionIds() {
			return polishingActionIds;
		},
		get polishErrors() {
			return polishErrors;
		},
		get executeError() {
			return executeError;
		},
		startEditing,
		cancelEditing,
		savePayload,
		polishDraft,
		executeAction,
		showInPulse
	};
</script>

<svelte:head>
	<title>{item?.text?.slice(0, 60) ?? 'Omni item'} - Laya</title>
</svelte:head>

<div
	class="-m-4 flex h-[calc(100%+2rem)] flex-col overflow-hidden"
	style="color: var(--om-text);"
	data-omni-density={$cardSize}
>
	<!-- B.1 Breadcrumb -->
	<div class="om-bar om-glass flex flex-none items-center gap-2.5 px-[18px] py-2">
		<button
			type="button"
			class="om-row-t inline-flex items-center gap-1.5 transition-colors"
			style="color: var(--om-text-body);"
			onclick={goBack}
		>
			<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
				<path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7" />
			</svg>
			Omni
		</button>
		{#if version}
			<span style="color: var(--om-text-faint);">/</span>
			<span class="om-mono text-[calc(10.5px*var(--om-scale))]" style="color: var(--om-text-meta);">
				v{version}
			</span>
		{/if}
		{#if section}
			<span style="color: var(--om-text-faint);">/</span>
			<span
				class="om-badge-lg rounded px-1.5 py-0.5"
				style="background: var(--om-warn-bg); color: var(--om-warn-fg);"
			>{layerLabel(section)}</span>
		{/if}
		<span class="om-row-t" style="color: var(--om-text-dim);">Omni item</span>

		<div class="flex-1"></div>

		<button
			type="button"
			class="om-row-t inline-flex items-center gap-1.5 rounded-[7px] px-2.5 py-1 transition-colors disabled:opacity-40"
			style="border: 1px solid var(--om-border-pill); color: var(--om-text-body);"
			disabled={cards.length === 0}
			onclick={() => openCardChat()}
		>
			<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.9 9.9 0 01-4-.8L3 20l1.3-3.2A7.6 7.6 0 013 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
				/>
			</svg>
			Chat about {cards.length === 1 ? 'this card' : `these ${cards.length} cards`}
		</button>
	</div>

	{#if error && cards.length === 0}
		<div class="flex flex-1 flex-col items-center justify-center gap-3">
			<p class="om-row-t" style="color: var(--om-alert-fg);">{error}</p>
			<button
				type="button"
				class="om-row-t rounded-lg px-4 py-2"
				style="background: var(--om-chip); color: var(--om-text-body);"
				onclick={load}
			>Retry</button>
		</div>
	{:else}
		<div class="flex min-h-0 flex-1" style="border-top: 1px solid var(--om-border);">
			<div class="flex min-w-0 flex-1 flex-col">
				<!-- The claim renders immediately; only the evidence skeletons while
				     cards fetch. The page never blanks. -->
				<ClaimHeader {item} {cards} {lineage} {sourceCardCount} />

				{#if cards.length > 0}
					<ClaimBreakdown
						{cards}
						activeFilter={$omniItemFilter}
						onFilter={setFilter}
						onSelectCard={selectCard}
					/>
				{/if}

				<EvidenceList
					bind:this={evidenceList}
					cards={filtered}
					totalCount={sourceCardCount}
					activeFilter={$omniItemFilter}
					expandedCardIds={$omniExpandedCards}
					showAll={$omniShowAllEvidence}
					{loading}
					onToggleCard={toggleCard}
					onExpandAll={expandCards}
					onCollapseAll={() => omniExpandedCards.set(new Set())}
					onShowAll={() => omniShowAllEvidence.set(true)}
					actions={actionContext}
				/>
			</div>

			<ItemContextRail
				{lineage}
				{version}
				{shareOfDay}
				{missingCardIds}
				{cards}
				onAsk={openCardChat}
				onShowMissing={() => (showMissing = !showMissing)}
			/>
		</div>
	{/if}

	{#if showMissing && missingCardIds.length > 0}
		<div
			class="om-inset om-glass om-entry-t absolute right-4 bottom-4 z-50 max-w-sm rounded-lg p-3"
			style="box-shadow: var(--om-popover-shadow); color: var(--om-text-body);"
			role="status"
		>
			<div class="om-micro mb-1.5">Dropped source cards</div>
			<ul class="om-mono flex flex-col gap-0.5 text-[calc(10px*var(--om-scale))]">
				{#each missingCardIds as id}
					<li>{id}</li>
				{/each}
			</ul>
			<button
				type="button"
				class="mt-2"
				style="color: var(--om-comp-label);"
				onclick={() => (showMissing = false)}
			>Close</button>
		</div>
	{/if}
</div>
