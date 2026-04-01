<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import {
		currentTrace,
		traceNarrativeStreamingMap,
		traceNarrativeMap,
		traceHistory,
		traceLoading,
		traceNewEventsDetected
	} from '$lib/stores/trace';
	import { get } from 'svelte/store';
	import { slide, fade } from 'svelte/transition';
	import TraceSearch from '$lib/components/trace/TraceSearch.svelte';
	import TraceHeader from '$lib/components/trace/TraceHeader.svelte';
	import TraceTimeline from '$lib/components/trace/TraceTimeline.svelte';
	import TraceHistory from '$lib/components/trace/TraceHistory.svelte';
	import type { TraceResponse } from '$lib/api/types';

	let loading = $state(false);
	let error = $state<string | null>(null);
	let trace = $state<TraceResponse | null>(get(currentTrace));
	let exporting = $state(false);
	let removedClusterIds = $state<Set<string>>(new Set());

	// Visible clusters = all clusters minus removed ones
	const visibleClusters = $derived(
		trace?.clusters.filter((c) => !removedClusterIds.has(c.cluster_id)) ?? []
	);

	// Aggregated metadata across all visible clusters
	const allPlatforms = $derived.by(() => {
		const platforms = new Set<string>();
		for (const c of visibleClusters) {
			for (const p of c.status_summary.platforms_involved) {
				platforms.add(p);
			}
		}
		return [...platforms].sort();
	});

	const dateRange = $derived.by(() => {
		let earliest = '';
		let latest = '';
		for (const c of visibleClusters) {
			const from = c.status_summary.date_range.from || '';
			const to = c.status_summary.date_range.to || '';
			if (from && (!earliest || from < earliest)) earliest = from;
			if (to && (!latest || to > latest)) latest = to;
		}
		return { from: earliest, to: latest };
	});

	const totalCards = $derived(
		visibleClusters.reduce((sum, c) => sum + c.status_summary.total_cards, 0)
	);

	// Tooltip state for top-level buttons
	let tooltip = $state<{ text: string; x: number; y: number } | null>(null);

	function showTooltip(e: MouseEvent, text: string) {
		const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
		tooltip = { text, x: rect.left + rect.width / 2, y: rect.top - 6 };
	}

	function hideTooltip() { tooltip = null; }

	// Load trace history on mount; restore narratives from persisted trace
	$effect(() => {
		loadHistory();
		const restored = get(currentTrace);
		if (restored) {
			trace = restored;
			const currentMap = get(traceNarrativeMap);
			const needsRestore = Object.keys(currentMap).length === 0;
			if (needsRestore) {
				const narratives: Record<string, string> = {};
				for (const c of restored.clusters) {
					if (c.narrative) narratives[c.cluster_id] = c.narrative;
				}
				if (Object.keys(narratives).length > 0) {
					traceNarrativeMap.set(narratives);
				}
			}
		}
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
				const cid = raw.cluster_id as string;
				traceNarrativeStreamingMap.update((m) => ({ ...m, [cid]: true }));
				traceNarrativeMap.update((m) => ({ ...m, [cid]: '' }));
			}
			return;
		}

		if (msg.type === 'trace_narrative_chunk') {
			_lastProcessedMsg = msg;
			const raw = msg as unknown as Record<string, unknown>;
			if (raw.trace_id === trace?.trace_id) {
				const cid = raw.cluster_id as string;
				const content = raw.content as string || '';
				traceNarrativeMap.update((m) => ({ ...m, [cid]: (m[cid] || '') + content }));
			}
			return;
		}

		if (msg.type === 'trace_narrative_done') {
			_lastProcessedMsg = msg;
			const raw = msg as unknown as Record<string, unknown>;
			if (raw.trace_id === trace?.trace_id) {
				const cid = raw.cluster_id as string;
				const narrative = raw.narrative as string || '';
				traceNarrativeStreamingMap.update((m) => ({ ...m, [cid]: false }));
				traceNarrativeMap.update((m) => ({ ...m, [cid]: narrative }));
				// Update the cluster's cached narrative
				if (trace) {
					const cluster = trace.clusters.find((c) => c.cluster_id === cid);
					if (cluster) {
						cluster.narrative = narrative;
						trace = trace; // trigger reactivity
					}
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

	async function handleSearch(query: string, fuzzy = false) {
		loading = true;
		error = null;
		removedClusterIds = new Set();
		traceNarrativeMap.set({});
		traceNarrativeStreamingMap.set({});
		traceNewEventsDetected.set(false);

		try {
			const result = await engineApi.runTrace(query, undefined, fuzzy);
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
		removedClusterIds = new Set();
		traceNarrativeMap.set({});
		traceNarrativeStreamingMap.set({});
		traceNewEventsDetected.set(false);

		try {
			const result = await engineApi.getTrace(traceId);
			trace = result;
			currentTrace.set(result);
			// Restore per-cluster narratives from cached data
			const narratives: Record<string, string> = {};
			for (const c of result.clusters) {
				if (c.narrative) narratives[c.cluster_id] = c.narrative;
			}
			traceNarrativeMap.set(narratives);
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
		removedClusterIds = new Set();
		traceNarrativeMap.set({});
		traceNarrativeStreamingMap.set({});
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

	async function handleRemoveCluster(clusterId: string) {
		if (!trace) return;
		// Immediately hide for smooth transition
		removedClusterIds = new Set([...removedClusterIds, clusterId]);
		// Persist removal to backend
		try {
			await engineApi.removeCluster(trace.trace_id, clusterId);
		} catch (e) {
			console.error('Remove cluster failed:', e);
		}
	}

	async function handleRestoreClusters() {
		if (!trace) return;
		removedClusterIds = new Set();
		try {
			await engineApi.restoreClusters(trace.trace_id);
		} catch (e) {
			console.error('Restore clusters failed:', e);
		}
	}

	async function handleGenerateNarrative(clusterId: string) {
		if (!trace) return;
		try {
			await engineApi.generateClusterNarrative(trace.trace_id, clusterId);
			// Narrative will stream via WebSocket
		} catch (e) {
			console.error('Narrative generation failed:', e);
		}
	}

	function handleBack() {
		trace = null;
		currentTrace.set(null);
		removedClusterIds = new Set();
		traceNarrativeMap.set({});
		traceNarrativeStreamingMap.set({});
		traceNewEventsDetected.set(false);
		error = null;
	}
</script>

<!-- Fixed-position tooltip -->
{#if tooltip}
	<div
		class="fixed z-50 px-2.5 py-1 rounded-md bg-surface-700 text-surface-100 text-xs font-medium shadow-lg pointer-events-none -translate-x-1/2 -translate-y-full"
		style="left: {tooltip.x}px; top: {tooltip.y}px;"
	>
		{tooltip.text}
	</div>
{/if}

<div class="min-h-screen bg-surface-900 p-6">
	<div class="max-w-4xl mx-auto">

		<!-- Header -->
		<div class="flex items-center justify-between mb-8">
			<div>
				<h1 class="text-2xl font-bold text-surface-50">Laya <span class="text-laya-orange">Coherence</span></h1>
				<p class="text-sm text-surface-500">
					{trace ? `"${trace.query}"` : 'Connect the dots across every platform'}
				</p>
			</div>
			{#if trace}
				<button
					onclick={handleBack}
					class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
					aria-label="Back to search"
				>
					<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
					</svg>
					Back
				</button>
			{/if}
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
		{#if trace && visibleClusters.length > 0}
			<div class="space-y-4">
				<!-- Search summary bar -->
				<div class="flex items-center justify-between rounded-lg bg-surface-800/60 border border-surface-700/50 px-4 py-2.5">
					<div class="flex items-center gap-3 text-xs text-surface-400">
						<span class="text-surface-200 font-medium">{totalCards} cards</span>
						<span class="text-surface-600">|</span>
						<span>{visibleClusters.length} {visibleClusters.length === 1 ? 'group' : 'groups'}</span>
						<span class="text-surface-600">|</span>
						<span>across {allPlatforms.map(p => p.charAt(0).toUpperCase() + p.slice(1)).join(', ')}</span>
						{#if dateRange.from}
							<span class="text-surface-600">|</span>
							<span>{dateRange.from}{dateRange.to && dateRange.to !== dateRange.from ? ` — ${dateRange.to}` : ''}</span>
						{/if}
						<span class="text-surface-600">|</span>
						<span class="text-surface-500">{trace.search_metadata.elapsed_ms}ms</span>
					</div>

					<button
						onclick={handleExport}
						onmouseenter={(e) => showTooltip(e, 'Download full coherence as Markdown')}
						onmouseleave={hideTooltip}
						disabled={exporting}
						class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
						       text-surface-300 bg-surface-700/60 border border-surface-600/50
						       hover:border-laya-orange/40 hover:text-laya-orange hover:bg-laya-orange/5
						       disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
					>
						<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
						</svg>
						{exporting ? 'Downloading...' : 'Export'}
					</button>
				</div>

				{#each visibleClusters as cluster (cluster.cluster_id)}
					<div transition:slide={{ duration: 300 }}>
						<!-- Header card with narrative -->
						<TraceHeader
							{cluster}
							onexport={handleExport}
							onrerun={() => handleRerun()}
							onremove={() => handleRemoveCluster(cluster.cluster_id)}
							ongenerate={() => handleGenerateNarrative(cluster.cluster_id)}
						/>

						<!-- Timeline (collapsible) -->
						<details class="group mt-3">
							<summary class="flex items-center gap-2 cursor-pointer select-none px-2 py-2 rounded-lg hover:bg-surface-800/60 transition-colors">
								<svg class="w-4 h-4 text-surface-500 transition-transform group-open:rotate-90" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
									<path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
								</svg>
								<span class="text-sm font-medium text-surface-400">
									{cluster.timeline.length} events
								</span>
								<span class="text-xs text-surface-500">
									— {cluster.status_summary.date_range.from} to {cluster.status_summary.date_range.to}
								</span>
							</summary>
							<div class="pl-2 pt-3">
								<TraceTimeline {cluster} />
							</div>
						</details>
					</div>
				{/each}
			</div>

		<!-- All clusters removed -->
		{:else if trace && trace.clusters.length > 0 && visibleClusters.length === 0}
			<div class="text-center py-12 text-surface-500" in:fade={{ duration: 250, delay: 300 }}>
				<p class="text-sm">All clusters have been removed.</p>
				<button
					onclick={handleRestoreClusters}
					class="mt-3 text-xs text-laya-orange hover:text-laya-gold transition-colors"
				>
					Restore all clusters
				</button>
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
