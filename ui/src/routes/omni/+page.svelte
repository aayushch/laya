<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import { spaces, loadSpaces } from '$lib/stores/spaces';
	import type { OmniSnapshot, OmniHistoryEntry, OmniItem as OmniItemType } from '$lib/api/types';
	import OmniView from '$lib/components/omni/OmniView.svelte';
	import OmniHeader from '$lib/components/omni/OmniHeader.svelte';
	import { goto } from '$app/navigation';
	import { onMount, onDestroy } from 'svelte';
	import { get } from 'svelte/store';
	import type { Unsubscriber } from 'svelte/store';
	import { omniSpace } from '$lib/stores/omniSpace';

	let snapshot = $state<OmniSnapshot | null>(null);
	let history = $state<OmniHistoryEntry[]>([]);
	let loading = $state(true);
	let resynthesizing = $state(false);
	let error = $state<string | null>(null);
	let activeSpaceId = $state(get(omniSpace));

	// Use store.subscribe instead of $effect to avoid Svelte 5 tracking
	// the state writes inside loadOmni/loadHistory, which causes infinite loops.
	let unsubWs: Unsubscriber;

	onMount(async () => {
		await loadSpaces();
		loadOmni();
		loadHistory();

		unsubWs = lastMessage.subscribe((msg) => {
			if (msg?.type === 'omni_updated') {
				loadOmni();
				loadHistory();
			}
		});
	});

	onDestroy(() => {
		unsubWs?.();
	});

	async function loadOmni(version?: number) {
		try {
			loading = !snapshot;
			error = null;
			snapshot = await engineApi.getOmni(activeSpaceId, version);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load Omni';
		} finally {
			loading = false;
		}
	}

	async function loadHistory() {
		try {
			const resp = await engineApi.getOmniHistory(activeSpaceId);
			history = resp.snapshots;
		} catch {
			// Non-critical
		}
	}

	async function handleResynthesis() {
		resynthesizing = true;
		try {
			await engineApi.triggerOmniResynthesis(activeSpaceId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Resynthesis failed';
		} finally {
			resynthesizing = false;
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

	function handleDrillDown(cardIds: string[]) {
		const params = new URLSearchParams();
		cardIds.forEach((id) => params.append('cards', id));
		goto(`/omni/insight?${params}`);
	}

	function switchSpace(spaceId: string) {
		activeSpaceId = spaceId;
		omniSpace.set(spaceId);
		// Reset and reload for the new space
		snapshot = null;
		history = [];
		loadOmni();
		loadHistory();
	}
</script>

<svelte:head>
	<title>Omni - Laya</title>
</svelte:head>

<div class="relative mx-auto max-w-5xl px-6 py-6">
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
			{history}
			{resynthesizing}
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
				/>
			</div>
		{/if}
	{/if}
</div>
