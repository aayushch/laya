<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import { lastMessage } from '$lib/stores/websocket';
	import {
		currentTrace,
		traceNarrativeStreamingMap,
		traceNarrativeMap,
		traceHistory,
		traceLoading,
		traceNewEventsDetected,
		traceProgress
	} from '$lib/stores/trace';
	import { get } from 'svelte/store';
	import { slide, fade } from 'svelte/transition';
	import TraceSearch from '$lib/components/trace/TraceSearch.svelte';
	import TraceHeader from '$lib/components/trace/TraceHeader.svelte';
	import TraceHistory from '$lib/components/trace/TraceHistory.svelte';
	import type { TraceResponse } from '$lib/api/types';

	let loading = $state(false);
	let error = $state<string | null>(null);
	let trace = $state<TraceResponse | null>(get(currentTrace));
	let exporting = $state(false);
	let removedClusterIds = $state<Set<string>>(new Set());
	let _activeTraceId = $state<string | null>(null);

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

	function formatDate(iso: string): string {
		if (!iso) return '';
		const d = new Date(iso + 'T00:00:00');
		const day = d.getDate();
		const month = d.toLocaleString(undefined, { month: 'short' });
		const year = d.getFullYear();
		const suffix = [11, 12, 13].includes(day % 100)
			? 'th'
			: ['th', 'st', 'nd', 'rd'][day % 10] ?? 'th';
		return `${day}${suffix} ${month} ${year}`;
	}

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

	const dateRangeText = $derived.by(() => {
		if (!dateRange.from) return '';
		const f = formatDate(dateRange.from);
		if (!dateRange.to || dateRange.to === dateRange.from) return f;
		const fd = new Date(dateRange.from + 'T00:00:00');
		const td = new Date(dateRange.to + 'T00:00:00');
		const t = formatDate(dateRange.to);
		if (fd.getFullYear() === td.getFullYear() && fd.getMonth() === td.getMonth()) {
			const fDay = fd.getDate();
			const fSuffix = [11, 12, 13].includes(fDay % 100)
				? 'th'
				: ['th', 'st', 'nd', 'rd'][fDay % 10] ?? 'th';
			return `${fDay}${fSuffix} — ${t}`;
		}
		if (fd.getFullYear() === td.getFullYear()) {
			const fDay = fd.getDate();
			const fSuffix = [11, 12, 13].includes(fDay % 100)
				? 'th'
				: ['th', 'st', 'nd', 'rd'][fDay % 10] ?? 'th';
			const fMonth = fd.toLocaleString(undefined, { month: 'short' });
			return `${fDay}${fSuffix} ${fMonth} — ${t}`;
		}
		return `${f} — ${t}`;
	});

	const totalCards = $derived(
		visibleClusters.reduce((sum, c) => sum + c.status_summary.total_cards, 0)
	);

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

		if (msg.type === 'trace_progress') {
			_lastProcessedMsg = msg;
			const raw = msg as unknown as Record<string, unknown>;
			const incomingId = raw.trace_id as string;
			// Defensive: trace_id is generated server-side so the frontend doesn't
			// know it upfront. We latch onto the first progress message's trace_id
			// and ignore messages from any other trace. This prevents a stale/concurrent
			// trace's progress from overwriting the UI when multiple searches overlap
			// (e.g. rapid re-runs before the previous one finishes).
			if (!_activeTraceId) _activeTraceId = incomingId;
			if (incomingId !== _activeTraceId) return;
			traceProgress.set({
				stage: raw.stage as string,
				step: raw.step as number,
				total: raw.total as number,
				query: raw.query as string,
			});
			return;
		}

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
				if (trace) {
					const cluster = trace.clusters.find((c) => c.cluster_id === cid);
					if (cluster) {
						cluster.narrative = narrative;
						trace = trace;
					}
				}
			}
			return;
		}

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
		_activeTraceId = null;
		traceProgress.set({ stage: 'Initializing', step: 0, total: 6, query });

		try {
			const result = await engineApi.runTrace(query, undefined, fuzzy);
			trace = result;
			currentTrace.set(result);

			if (result.clusters.length === 0) {
				error = 'No results found. Try a different search term.';
			}

			loadHistory();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Search failed';
		} finally {
			loading = false;
			traceProgress.set(null);
		}
	}

	async function handleSelectTrace(traceId: string) {
		loading = true;
		error = null;
		removedClusterIds = new Set();
		traceNarrativeMap.set({});
		traceNarrativeStreamingMap.set({});
		traceNewEventsDetected.set(false);
		traceProgress.set(null);

		try {
			const result = await engineApi.getTrace(traceId);
			trace = result;
			currentTrace.set(result);
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
		_activeTraceId = null;
		traceProgress.set({ stage: 'Initializing', step: 0, total: 6, query: trace?.query ?? '' });

		try {
			const result = await engineApi.rerunTrace(id);
			trace = result;
			currentTrace.set(result);
			loadHistory();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Rerun failed';
		} finally {
			loading = false;
			traceProgress.set(null);
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
		removedClusterIds = new Set([...removedClusterIds, clusterId]);
		try {
			await engineApi.removeCluster(trace.trace_id, clusterId);
			// Persist removal in the store so navigating away/back doesn't restore it
			const updated = { ...trace, clusters: trace.clusters.filter(c => c.cluster_id !== clusterId) };
			currentTrace.set(updated);
		} catch (e) {
			console.error('Remove cluster failed:', e);
		}
	}

	async function handleRestoreClusters() {
		if (!trace) return;
		removedClusterIds = new Set();
		try {
			await engineApi.restoreClusters(trace.trace_id);
			// Re-fetch the full trace so restored clusters are back in the store
			const result = await engineApi.getTrace(trace.trace_id);
			trace = result;
			currentTrace.set(result);
		} catch (e) {
			console.error('Restore clusters failed:', e);
		}
	}

	async function handleGenerateNarrative(clusterId: string) {
		if (!trace) return;
		try {
			await engineApi.generateClusterNarrative(trace.trace_id, clusterId);
		} catch (e) {
			console.error('Narrative generation failed:', e);
		}
	}

	const coherenceStages = [
		'Searching',
		'Ranking results',
		'Applying feedback',
		'Expanding results',
		'Analyzing connections',
		'Building clusters',
	];

	function handleBack() {
		trace = null;
		currentTrace.set(null);
		removedClusterIds = new Set();
		traceNarrativeMap.set({});
		traceNarrativeStreamingMap.set({});
		traceNewEventsDetected.set(false);
		traceProgress.set(null);
		error = null;
	}
</script>

<div class="min-h-screen bg-surface-900 p-6">
	<div class="max-w-5xl mx-auto">

		<!-- Header -->
		<div class="flex items-center justify-between mb-6">
			<div>
				<h1 class="text-xl font-bold text-surface-50">Laya <span class="text-laya-orange">Coherence</span><sup class="text-[9px] ml-1 text-surface-500 tracking-wider font-medium">BETA</sup></h1>
				<p class="text-xs text-surface-500 mt-0.5">
					{trace ? `"${trace.query}"` : 'Connect the dots across every platform'}
				</p>
			</div>
			{#if trace}
				<button
					onclick={handleBack}
					class="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
					aria-label="Back to search"
				>
					<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
					</svg>
					Back
				</button>
			{/if}
		</div>

		<!-- Search bar -->
		<div class="mb-6">
			<TraceSearch
				onsubmit={handleSearch}
				{loading}
				initialQuery={trace?.query || ''}
			/>
		</div>

		<!-- Error -->
		{#if error}
			<div class="rounded-md border border-red-500/30 bg-red-500/10 p-3 mb-4">
				<p class="text-xs text-red-400">{error}</p>
			</div>
		{/if}

		<!-- New events banner -->
		{#if $traceNewEventsDetected && trace}
			<button
				onclick={() => handleRerun()}
				class="w-full rounded-md border border-laya-orange/30 bg-laya-orange/10 p-2 mb-4
				       flex items-center justify-center gap-1.5 hover:bg-laya-orange/20 transition-colors cursor-pointer"
			>
				<svg class="w-3.5 h-3.5 text-laya-orange" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
				</svg>
				<span class="text-xs text-laya-orange font-medium">New events detected — Click to refresh</span>
			</button>
		{/if}

		<!-- Active trace: tree view -->
		{#if trace && visibleClusters.length > 0}
			<!-- Summary bar -->
			<div class="flex items-center justify-between rounded-md bg-surface-800/60 border border-surface-700/50 px-3 py-2 mb-4">
				<div class="flex items-center gap-2 text-[11px] text-surface-400">
					<span class="text-surface-200 font-medium">{totalCards} cards</span>
					<span class="text-surface-600">·</span>
					<span>{visibleClusters.length} {visibleClusters.length === 1 ? 'cluster' : 'clusters'}</span>
					<span class="text-surface-600">·</span>
					<span>{allPlatforms.map(p => p.charAt(0).toUpperCase() + p.slice(1)).join(', ')}</span>
					{#if dateRangeText}
						<span class="text-surface-600">·</span>
						<span>{dateRangeText}</span>
					{/if}
					<span class="text-surface-600">·</span>
					<span class="text-surface-500 tabular-nums">{trace.search_metadata.elapsed_ms}ms</span>
				</div>

				<div class="flex items-center gap-1.5">
					{#if removedClusterIds.size > 0}
						<button
							onclick={handleRestoreClusters}
							class="text-[11px] text-surface-500 hover:text-laya-orange transition-colors"
						>
							restore {removedClusterIds.size}
						</button>
						<span class="text-surface-600">·</span>
					{/if}
					<button
						onclick={() => handleRerun()}
						class="p-1 rounded text-surface-400 hover:text-surface-200 hover:bg-surface-700 transition-colors"
						title="Re-run trace"
					>
						<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
						</svg>
					</button>
					<button
						onclick={handleExport}
						disabled={exporting}
						class="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium
						       text-surface-300 bg-surface-700/60 border border-surface-600/50
						       hover:border-laya-orange/40 hover:text-laya-orange hover:bg-laya-orange/5
						       disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
					>
						<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
						</svg>
						{exporting ? '...' : 'Export'}
					</button>
				</div>
			</div>

			<!-- Tree structure -->
			<div class="rounded-md border border-surface-700/50 bg-surface-800/30 px-4 py-2.5 overflow-hidden">
				<!-- Root node -->
				<div class="flex items-center gap-1.5 pb-2 mb-1 border-b border-surface-700/30">
					<span class="text-laya-orange text-[13px]">◆</span>
					<span class="text-[13px] font-medium text-surface-200">{trace.query}</span>
					<span class="text-[11px] text-surface-500 ml-1">{visibleClusters.length} clusters</span>
				</div>

				<!-- Cluster nodes -->
				<div class="relative">
					{#each visibleClusters as cluster, idx (cluster.cluster_id)}
						<div transition:slide={{ duration: 200 }}>
							<TraceHeader
								{cluster}
								onremove={() => handleRemoveCluster(cluster.cluster_id)}
								ongenerate={() => handleGenerateNarrative(cluster.cluster_id)}
								isLast={idx === visibleClusters.length - 1}
							/>
						</div>
					{/each}
				</div>
			</div>

		<!-- All clusters removed -->
		{:else if trace && trace.clusters.length > 0 && visibleClusters.length === 0}
			<div class="text-center py-10 text-surface-500" in:fade={{ duration: 250, delay: 300 }}>
				<p class="text-xs">All clusters removed.</p>
				<button
					onclick={handleRestoreClusters}
					class="mt-2 text-xs text-laya-orange hover:text-laya-gold transition-colors"
				>
					Restore all
				</button>
			</div>

		<!-- Empty state with trace history -->
		{:else if !trace && !loading}
			<div class="mt-4">
				<h2 class="text-xs font-medium text-surface-400 uppercase tracking-wider mb-3">
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

		<!-- Loading progress -->
		{#if loading && !trace}
			<div class="mt-6 rounded-xl border border-surface-700/50 bg-surface-800/40 p-6" in:fade={{ duration: 200 }}>
				<!-- Query title -->
				<div class="flex items-center gap-2 mb-5">
					<span class="text-laya-orange text-sm">◆</span>
					<span class="text-sm font-medium text-surface-200">{$traceProgress?.query || 'Searching...'}</span>
				</div>

				<!-- Progress bar -->
				<div class="mb-4">
					<div class="h-1.5 w-full rounded-full bg-surface-700/60 overflow-hidden">
						<div
							class="h-full rounded-full bg-gradient-to-r from-laya-orange to-laya-gold transition-all duration-500 ease-out"
							style="width: {$traceProgress ? Math.max(($traceProgress.step / $traceProgress.total) * 100, 8) : 8}%"
						></div>
					</div>
				</div>

				<!-- Current stage -->
				<div class="flex items-center gap-2 text-xs text-surface-400">
					<svg class="w-3.5 h-3.5 animate-spin shrink-0 text-laya-orange" fill="none" viewBox="0 0 24 24">
						<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3"></circle>
						<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
					</svg>
					<span class="font-medium transition-all duration-300">{($traceProgress?.step ?? 0) > 0 && ($traceProgress?.step ?? 0) <= coherenceStages.length ? coherenceStages[($traceProgress?.step ?? 1) - 1] : 'Preparing'}</span>
					<span class="text-surface-600">&middot;</span>
					<span class="text-surface-500">{$traceProgress?.step ?? 0} / {$traceProgress?.total ?? coherenceStages.length}</span>
				</div>
			</div>
		{/if}

	</div>
</div>
