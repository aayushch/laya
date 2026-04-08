<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import { spaces, loadSpaces } from '$lib/stores/spaces';
	import type { OmniSnapshot, OmniItem as OmniItemType, TimelineSegment } from '$lib/api/types';
	import OmniView from '$lib/components/omni/OmniView.svelte';
	import OmniHeader from '$lib/components/omni/OmniHeader.svelte';
	import { goto } from '$app/navigation';
	import { onMount, onDestroy, tick } from 'svelte'; // tick used for scroll restore
	import { get } from 'svelte/store';
	import type { Unsubscriber } from 'svelte/store';
	import { omniSpace } from '$lib/stores/omniSpace';
	import { resynthesizingSpaces, markResynthesizing, clearResynthesizing } from '$lib/stores/omniResynthesis';
	import type { Settings } from '$lib/api/types';

	const SCROLL_TARGET_KEY = 'laya_omni_scroll_target';

	let snapshot = $state<OmniSnapshot | null>(null);
	let segments = $state<TimelineSegment[]>([]);
	let loading = $state(true);
	let resynthesizing = $state(false);
	let error = $state<string | null>(null);
	let activeSpaceId = $state(get(omniSpace));
	let nextSynthesis = $state<string | null>(null);

	// Use store.subscribe instead of $effect to avoid Svelte 5 tracking
	// the state writes inside loadOmni/loadHistory, which causes infinite loops.
	let unsubWs: Unsubscriber;
	let unsubResynth: Unsubscriber;

	async function loadNextSynthesisTime() {
		try {
			const settings: Settings = await engineApi.getSettings();
			const omniCfg = settings.omni;
			if (!omniCfg?.enabled) { nextSynthesis = null; return; }

			const now = new Date();
			const candidates: Date[] = [];

			// (1) EOD scheduled resynthesis
			if (omniCfg.resynthesis_time) {
				const [h, m] = omniCfg.resynthesis_time.split(':').map(Number);
				const eod = new Date(now);
				eod.setHours(h, m, 0, 0);
				if (eod <= now) eod.setDate(eod.getDate() + 1);
				candidates.push(eod);
			}

			// (2) Rolling interval
			const rollingHours = omniCfg.rolling_interval_hours ?? 0;
			if (rollingHours > 0 && snapshot?.generated_at) {
				const lastGen = new Date(snapshot.generated_at);
				const rolling = new Date(lastGen.getTime() + rollingHours * 3600000);
				if (rolling > now) candidates.push(rolling);
				else candidates.push(new Date(now.getTime() + 60000)); // imminent
			}

			if (candidates.length === 0) { nextSynthesis = null; return; }
			const nearest = candidates.reduce((a, b) => a < b ? a : b);
			const diffMs = nearest.getTime() - now.getTime();
			const diffMins = Math.floor(diffMs / 60000);
			if (diffMins < 1) nextSynthesis = 'imminent';
			else if (diffMins < 60) nextSynthesis = `in ${diffMins}m`;
			else {
				const diffHours = Math.floor(diffMins / 60);
				if (diffHours < 24) nextSynthesis = `in ${diffHours}h ${diffMins % 60}m`;
				else nextSynthesis = nearest.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
			}
		} catch {
			nextSynthesis = null;
		}
	}

	onMount(async () => {
		await loadSpaces();
		await loadOmni();
		loadTimeline();

		// Scroll to the item user was viewing before navigating to insight
		const scrollTarget = sessionStorage.getItem(SCROLL_TARGET_KEY);
		if (scrollTarget) {
			sessionStorage.removeItem(SCROLL_TARGET_KEY);
			await tick();
			scrollToItem(scrollTarget);
		}

		unsubWs = lastMessage.subscribe((msg) => {
			if (msg?.type === 'omni_updated') {
				// Resynthesis completed — clear the flag and reload
				clearResynthesizing(activeSpaceId);
				loadOmni();
				loadTimeline();
			}
		});

		// Track resynthesis state from store so it survives navigation
		unsubResynth = resynthesizingSpaces.subscribe((set) => {
			resynthesizing = set.has(activeSpaceId);
		});
	});

	onDestroy(() => {
		unsubWs?.();
		unsubResynth?.();
	});

	async function loadOmni(version?: number) {
		try {
			loading = !snapshot;
			error = null;
			snapshot = await engineApi.getOmni(activeSpaceId, version);
			loadNextSynthesisTime();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load Omni';
		} finally {
			loading = false;
		}
	}

	async function loadTimeline() {
		try {
			const resp = await engineApi.getOmniTimeline(activeSpaceId);
			segments = resp.segments;
		} catch {
			// Non-critical
		}
	}

	async function handleResynthesis() {
		markResynthesizing(activeSpaceId);
		try {
			// Backend returns 202 immediately — resynthesis runs in background.
			// The omni_updated WebSocket event will clear the flag and reload.
			await engineApi.triggerOmniResynthesis(activeSpaceId);
		} catch (e) {
			const msg = e instanceof Error ? e.message : 'Resynthesis failed';
			// 409 = already in progress — keep the flag set, don't show error
			if (msg.includes('already in progress')) return;
			clearResynthesizing(activeSpaceId);
			error = msg;
		}
	}

	function handleVersionChange(version: number) {
		loadOmni(version);
	}

	async function handlePin(item: OmniItemType) {
		try {
			await engineApi.pinOmniItem({
				space_id: activeSpaceId,
				text: item.text,
				source_cards: item.source_cards,
				platforms: item.platforms
			});
			item.pinned = true;
			snapshot = snapshot ? { ...snapshot } : null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to pin item';
		}
	}

	async function handleUnpin(item: OmniItemType) {
		try {
			const pinsResp = await engineApi.getOmniPins(activeSpaceId);
			const matchingPin = pinsResp.pins.find((p) => p.item_text === item.text);
			if (matchingPin) {
				await engineApi.unpinOmniItem(matchingPin.pin_id);
				item.pinned = false;
				snapshot = snapshot ? { ...snapshot } : null;
			}
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to unpin item';
		}
	}

	async function handleBookmark(item: OmniItemType) {
		const sourceCardId = item.source_cards[0];
		if (!sourceCardId) return;
		const newState = !item.bookmarked;
		try {
			await engineApi.toggleOmniBookmark({
				space_id: activeSpaceId,
				source_card_id: sourceCardId,
				bookmarked: newState
			});
			item.bookmarked = newState;
			snapshot = snapshot ? { ...snapshot } : null;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to toggle bookmark';
		}
	}

	function handleDrillDown(cardIds: string[]) {
		if (cardIds[0]) {
			sessionStorage.setItem(SCROLL_TARGET_KEY, cardIds[0]);
		}
		const params = new URLSearchParams();
		cardIds.forEach((id) => params.append('cards', id));
		goto(`/omni/insight?${params}`);
	}

	/** Scroll to item and apply highlight animation. */
	function scrollToItem(itemId: string) {
		const attempt = (tries: number) => {
			requestAnimationFrame(() => {
				const el = document.querySelector(`[data-omni-item="${itemId}"]`);
				if (el) {
					el.scrollIntoView({ behavior: 'smooth', block: 'center' });
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
	}

	function switchSpace(spaceId: string) {
		activeSpaceId = spaceId;
		omniSpace.set(spaceId);
		// Reset and reload for the new space
		snapshot = null;
		segments = [];
		loadOmni();
		loadTimeline();
	}
</script>

<svelte:head>
	<title>Omni - Laya</title>
</svelte:head>

<div class="relative min-h-screen bg-surface-900 p-6">
<div class="max-w-5xl mx-auto">
	<!-- Loading overlay -->
	{#if loading && !snapshot}
		<div class="absolute inset-0 z-10 flex items-start justify-center pt-20">
			<div class="flex items-center gap-2">
				<svg class="h-4 w-4 animate-spin text-laya-orange" fill="none" viewBox="0 0 24 24">
					<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
					<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
				</svg>
				<span class="text-sm text-surface-500">Loading Omni...</span>
			</div>
		</div>
	{/if}
	{#if error && !snapshot}
		<div class="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
			{error}
		</div>
	{/if}
	{#if snapshot}
		<!-- Header with controls -->
		<OmniHeader
			version={snapshot.version}
			generatedAt={snapshot.generated_at}
			snapshotType={snapshot.snapshot_type}
			stats={snapshot.stats ?? { events_processed: 0, cards_acted_on: 0, compression_ratio: 0 }}
			{segments}
			{resynthesizing}
			{nextSynthesis}
			spaces={$spaces}
			{activeSpaceId}
			onVersionChange={handleVersionChange}
			onResynthesis={handleResynthesis}
			onSpaceChange={switchSpace}
		/>

		<!-- Summary view -->
		{#if snapshot.sections.length === 0 && snapshot.version === 0}
			<!-- Empty state -->
			<div class="mt-12 flex flex-col items-center gap-4 text-center">
				<div class="flex h-16 w-16 items-center justify-center rounded-full bg-surface-800">
					<svg class="h-8 w-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
						<path d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2z"/>
						<path d="M12 6v6l4 2"/>
					</svg>
				</div>
				<div>
					<h3 class="text-lg font-semibold text-surface-200">Omni is warming up</h3>
					<p class="mt-1 text-sm text-surface-500 max-w-sm">
						As Laya processes events, Omni will build a rolling summary of your professional activity across all platforms.
					</p>
				</div>
				<button
					onclick={handleResynthesis}
					disabled={resynthesizing}
					class="mt-2 rounded-lg bg-laya-orange/15 px-4 py-2 text-sm font-medium text-laya-orange transition-colors hover:bg-laya-orange/25 disabled:opacity-50"
				>
					{resynthesizing ? 'Synthesizing...' : 'Generate first summary'}
				</button>
			</div>
		{:else}
			<div class="mt-4">
				<OmniView
					{snapshot}
					onPin={handlePin}
					onUnpin={handleUnpin}
					onDrillDown={handleDrillDown}
					onBookmark={handleBookmark}
				/>
			</div>
		{/if}
	{/if}
</div>
</div>
