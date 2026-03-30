<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import {
		currentTrace,
		traceNarrativeStreaming,
		traceNarrative,
		traceHistory,
		traceLoading,
		traceNewEventsDetected
	} from '$lib/stores/trace';
	import { get } from 'svelte/store';
	import TraceSearch from '$lib/components/trace/TraceSearch.svelte';
	import TraceHeader from '$lib/components/trace/TraceHeader.svelte';
	import TraceTimeline from '$lib/components/trace/TraceTimeline.svelte';
	import TraceHistory from '$lib/components/trace/TraceHistory.svelte';
	import type { TraceResponse } from '$lib/api/types';

	let loading = $state(false);
	let error = $state<string | null>(null);
	let trace = $state<TraceResponse | null>(null);
	let exporting = $state(false);

	// Load trace history on mount
	$effect(() => {
		loadHistory();
	});

	// WebSocket handler for narrative streaming and new events
	let _lastProcessedMsg: unknown = null;
	$effect(() => {
		const msg = $lastMessage;
		if (!msg || msg === _lastProcessedMsg) return;

		if (msg.type === 'trace_narrative_start') {
			_lastProcessedMsg = msg;
			const raw = msg as unknown as Record<string, unknown>;
			if (raw.trace_id === trace?.trace_id) {
				traceNarrativeStreaming.set(true);
				traceNarrative.set('');
			}
			return;
		}

		if (msg.type === 'trace_narrative_chunk') {
			_lastProcessedMsg = msg;
			const raw = msg as unknown as Record<string, unknown>;
			if (raw.trace_id === trace?.trace_id) {
				traceNarrative.update((n) => n + (raw.content as string || ''));
			}
			return;
		}

		if (msg.type === 'trace_narrative_done') {
			_lastProcessedMsg = msg;
			const raw = msg as unknown as Record<string, unknown>;
			if (raw.trace_id === trace?.trace_id) {
				traceNarrativeStreaming.set(false);
				const narrative = raw.narrative as string || '';
				traceNarrative.set(narrative);
				// Update the trace clusters with the narrative
				if (trace && trace.clusters.length > 0) {
					trace.clusters[0].narrative = narrative;
					trace = trace; // trigger reactivity
				}
			}
			return;
		}

		// Detect new events for traced entities
		if (trace && (msg.type === 'card_created' || msg.type === 'card_updated')) {
			_lastProcessedMsg = msg;
			const raw = msg as unknown as Record<string, unknown>;
			const payload = raw.payload as Record<string, unknown> | undefined;
			const cardEntityId = payload?.entity_id as string | undefined;
			if (cardEntityId) {
				const tracedEntities = new Set<string>();
				for (const cluster of trace.clusters) {
					tracedEntities.add(cluster.primary_entity.entity_id);
					for (const linked of cluster.linked_entities) {
						tracedEntities.add(linked.entity_id);
					}
				}
				if (tracedEntities.has(cardEntityId)) {
					traceNewEventsDetected.set(true);
				}
			}
			return;
		}
	});

	async function loadHistory() {
		try {
			const history = await engineApi.getTraces(30);
			traceHistory.set(history);
		} catch {
			// Silent — history is optional
		}
	}

	async function handleSearch(query: string) {
		loading = true;
		error = null;
		traceNarrative.set('');
		traceNarrativeStreaming.set(false);
		traceNewEventsDetected.set(false);

		try {
			const result = await engineApi.runTrace(query);
			trace = result;
			currentTrace.set(result);

			if (result.clusters.length === 0) {
				error = 'No results found. Try a different search term.';
			}

			// Refresh history
			loadHistory();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Search failed';
		} finally {
			loading = false;
		}
	}

	async function handleSelectTrace(traceId: string) {
		loading = true;
		error = null;
		traceNarrative.set('');
		traceNarrativeStreaming.set(false);
		traceNewEventsDetected.set(false);

		try {
			const result = await engineApi.getTrace(traceId);
			trace = result;
			currentTrace.set(result);
			// Set narrative from cached data
			if (result.clusters.length > 0 && result.clusters[0].narrative) {
				traceNarrative.set(result.clusters[0].narrative);
			}
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load trace';
		} finally {
			loading = false;
		}
	}

	async function handleRerun(traceId?: string) {
		const id = traceId || trace?.trace_id;
		if (!id) return;

		loading = true;
		error = null;
		traceNarrative.set('');
		traceNarrativeStreaming.set(false);
		traceNewEventsDetected.set(false);

		try {
			const result = await engineApi.rerunTrace(id);
			trace = result;
			currentTrace.set(result);
			loadHistory();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Rerun failed';
		} finally {
			loading = false;
		}
	}

	async function handleDelete(traceId: string) {
		try {
			await engineApi.deleteTrace(traceId);
			if (trace?.trace_id === traceId) {
				trace = null;
				currentTrace.set(null);
			}
			loadHistory();
		} catch {
			// Silent
		}
	}

	async function handleExport() {
		if (!trace || exporting) return;
		exporting = true;
		try {
			const blob = await engineApi.exportTrace(trace.trace_id);
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `coherence-${trace.query.replace(/[^a-z0-9]/gi, '-').slice(0, 40)}.md`;
			a.click();
			URL.revokeObjectURL(url);
		} catch (e) {
			console.error('Export failed:', e);
		} finally {
			exporting = false;
		}
	}

	function handleBack() {
		trace = null;
		currentTrace.set(null);
		traceNarrative.set('');
		traceNarrativeStreaming.set(false);
		traceNewEventsDetected.set(false);
		error = null;
	}
</script>

<div class="min-h-screen bg-surface-900 p-6">
	<div class="max-w-4xl mx-auto">

		<!-- Header -->
		<div class="flex items-center gap-3 mb-8">
			{#if trace}
				<button
					onclick={handleBack}
					class="p-2 rounded-lg text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
					aria-label="Back to search"
				>
					<svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
					</svg>
				</button>
			{/if}
			<div>
				<h1 class="text-2xl font-bold text-surface-50">Laya <span class="text-laya-orange">Coherence</span></h1>
				<p class="text-sm text-surface-500">
					{trace ? `"${trace.query}"` : 'Connect the dots across every platform'}
				</p>
			</div>
		</div>

		<!-- Search bar -->
		<div class="mb-8">
			<TraceSearch
				onsubmit={handleSearch}
				{loading}
				initialQuery={trace?.query || ''}
			/>
		</div>

		<!-- Error -->
		{#if error}
			<div class="rounded-lg border border-red-500/30 bg-red-500/10 p-4 mb-6">
				<p class="text-sm text-red-400">{error}</p>
			</div>
		{/if}

		<!-- New events banner -->
		{#if $traceNewEventsDetected && trace}
			<button
				onclick={() => handleRerun()}
				class="w-full rounded-lg border border-laya-orange/30 bg-laya-orange/10 p-3 mb-6
				       flex items-center justify-center gap-2 hover:bg-laya-orange/20 transition-colors cursor-pointer"
			>
				<svg class="w-4 h-4 text-laya-orange" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
				</svg>
				<span class="text-sm text-laya-orange font-medium">New events detected for this entity — Click to refresh</span>
			</button>
		{/if}

		<!-- Active trace view -->
		{#if trace && trace.clusters.length > 0}
			<div class="space-y-6">
				<!-- Search metadata -->
				<div class="flex items-center gap-4 text-xs text-surface-500">
					<span>{trace.search_metadata.expansion_cards} cards found</span>
					<span>in {trace.search_metadata.elapsed_ms}ms</span>
					<span>
						(semantic: {trace.search_metadata.semantic_hits},
						fuzzy: {trace.search_metadata.fuzzy_hits},
						entity: {trace.search_metadata.entity_hits})
					</span>
				</div>

				{#each trace.clusters as cluster}
					<!-- Header card with narrative -->
					<TraceHeader
						{cluster}
						onexport={handleExport}
						onrerun={() => handleRerun()}
					/>

					<!-- Timeline -->
					<div class="pl-2">
						<TraceTimeline {cluster} />
					</div>
				{/each}
			</div>

		<!-- Empty state with trace history -->
		{:else if !trace && !loading}
			<div class="mt-4">
				<h2 class="text-sm font-medium text-surface-400 uppercase tracking-wider mb-4">
					Recent Searches
				</h2>
				<TraceHistory
					traces={$traceHistory}
					onselect={handleSelectTrace}
					ondelete={handleDelete}
					onrerun={handleRerun}
				/>
			</div>
		{/if}

		<!-- Loading skeleton -->
		{#if loading && !trace}
			<div class="space-y-4 animate-pulse mt-4">
				<div class="rounded-xl border border-surface-700 bg-surface-800/40 p-5 h-40"></div>
				<div class="space-y-3">
					{#each Array(4) as _}
						<div class="ml-6 rounded-lg border border-surface-700/40 bg-surface-800/30 p-3 h-20"></div>
					{/each}
				</div>
			</div>
		{/if}

	</div>
</div>
