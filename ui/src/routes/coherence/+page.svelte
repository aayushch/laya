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
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import TraceSearch from '$lib/components/trace/TraceSearch.svelte';
	import TraceHeader from '$lib/components/trace/TraceHeader.svelte';
	import TraceHistory from '$lib/components/trace/TraceHistory.svelte';
	import type { TraceResponse } from '$lib/api/types';

	// Use the persistent store so loading state survives navigation away and back.
	// `loading` reflects an in-flight coherence search specifically — NOT a quick
	// DB read in handleSelectTrace, which uses its own local flag. Keeping this
	// distinct ensures the in-flight card in Recent Searches survives while the
	// user pokes around other traces.
	const loading = $derived($traceLoading);
	let cancelling = $state(false);
	let error = $state<string | null>(null);
	let trace = $state<TraceResponse | null>(get(currentTrace));
	let exporting = $state(false);
	let selectingTrace = $state(false);
	let removedClusterIds = $state<Set<string>>(new Set());
	let _activeTraceId = $state<string | null>(null);
	let expandAllClusters = $state<boolean | null>(null);
	let clustersExpanded = $state(false);
	// When true the user clicked Back during an in-flight search: the search keeps
	// running in the background but we render the Recent Searches view (with an
	// in-flight card) instead of the progress bar.
	let showHistoryDuringSearch = $state(false);

	// Laya-style hover tooltip (mirrors the pattern used in TraceHeader/TraceHistory)
	// instead of relying on the browser's native `title` attribute, which doesn't
	// match the app's visual language.
	let tooltip = $state<{ text: string; x: number; y: number } | null>(null);
	function showTooltip(e: MouseEvent, text: string) {
		const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
		tooltip = { text, x: rect.left + rect.width / 2, y: rect.top - 6 };
	}
	function hideTooltip() { tooltip = null; }

	// Reset expand/collapse state when switching to a different query
	let prevTraceId = $state<string | null>(null);
	$effect(() => {
		const currentId = trace?.trace_id ?? null;
		if (currentId !== prevTraceId) {
			clustersExpanded = false;
			expandAllClusters = null;
			prevTraceId = currentId;
		}
	});

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

	// Narrative map keys are scoped by trace_id so in-flight streaming state
	// survives navigation (Back then re-select) and can't collide across traces.
	function narrativeKey(traceId: string, clusterId: string): string {
		return `${traceId}:${clusterId}`;
	}

	// Load trace history on mount; restore narratives from persisted trace
	$effect(() => {
		loadHistory();
		const restored = get(currentTrace);
		if (restored) {
			trace = restored;
			traceNarrativeMap.update((current) => {
				const merged = { ...current };
				for (const c of restored.clusters) {
					const key = narrativeKey(restored.trace_id, c.cluster_id);
					if (c.narrative && !merged[key]) merged[key] = c.narrative;
				}
				const sk = narrativeKey(restored.trace_id, SUMMARY_ID);
				if (restored.summary && !merged[sk]) merged[sk] = restored.summary;
				return merged;
			});
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
			const tid = raw.trace_id as string;
			const cid = raw.cluster_id as string;
			// Always update the map — keys are scoped by trace_id, so messages arriving
			// while the user has navigated to the history view (trace === null) still
			// land in the right bucket and are visible when they return.
			const key = narrativeKey(tid, cid);
			traceNarrativeStreamingMap.update((m) => ({ ...m, [key]: true }));
			traceNarrativeMap.update((m) => ({ ...m, [key]: '' }));
			return;
		}

		if (msg.type === 'trace_narrative_chunk') {
			_lastProcessedMsg = msg;
			const raw = msg as unknown as Record<string, unknown>;
			const tid = raw.trace_id as string;
			const cid = raw.cluster_id as string;
			const content = raw.content as string || '';
			const key = narrativeKey(tid, cid);
			traceNarrativeMap.update((m) => ({ ...m, [key]: (m[key] || '') + content }));
			return;
		}

		if (msg.type === 'trace_narrative_done') {
			_lastProcessedMsg = msg;
			const raw = msg as unknown as Record<string, unknown>;
			const tid = raw.trace_id as string;
			const cid = raw.cluster_id as string;
			const narrative = raw.narrative as string || '';
			const key = narrativeKey(tid, cid);
			traceNarrativeStreamingMap.update((m) => ({ ...m, [key]: false }));
			traceNarrativeMap.update((m) => ({ ...m, [key]: narrative }));
			// Persist into the currentTrace only if the message is for the currently
			// loaded trace — otherwise the backend's DB write already covers it on next load.
			if (trace && trace.trace_id === tid) {
				if (cid === SUMMARY_ID) {
					trace = { ...trace, summary: narrative };
					currentTrace.set(trace);
				} else {
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

	async function handleSearch(query: string, fuzzy = false, opts?: {
		enableSemantic?: boolean;
		enableText?: boolean;
		enableLlmFilter?: boolean;
	}) {
		traceLoading.set(true);
		error = null;
		// Clear the active trace so the progress bar shows immediately
		// instead of staying on stale results while the new search runs.
		trace = null;
		currentTrace.set(null);
		removedClusterIds = new Set();
		traceNarrativeMap.set({});
		traceNarrativeStreamingMap.set({});
		traceNewEventsDetected.set(false);
		_activeTraceId = null;
		showHistoryDuringSearch = false;
		traceProgress.set({ stage: 'Initializing', step: 0, total: 6, query });

		try {
			const result = await engineApi.runTrace(query, undefined, fuzzy, opts);
			// Only adopt the result as the current view if the user is still watching
			// this search. If they've navigated elsewhere (backed out to history, or
			// opened another trace), leave their view alone and just refresh history
			// so they can find the new result there.
			const userStillWatching = !trace && !showHistoryDuringSearch;
			if (userStillWatching) {
				trace = result;
				currentTrace.set(result);
				if (result.clusters.length === 0) {
					error = 'No results found. Try a different search term.';
				}
			}

			loadHistory();
		} catch (e) {
			const msg = e instanceof Error ? e.message : 'Search failed';
			// Don't show error for cancellation, and only surface it if the user is
			// still on the progress view for this search.
			if (!msg.includes('cancelled') && !msg.includes('abort')) {
				if (!trace && !showHistoryDuringSearch) {
					error = msg;
				}
			}
		} finally {
			traceLoading.set(false);
			cancelling = false;
			traceProgress.set(null);
			showHistoryDuringSearch = false;
		}
	}

	async function handleCancelSearch() {
		cancelling = true;
		try {
			await engineApi.cancelTrace();
		} catch {
			// Best-effort — the search may already have completed
		}
	}

	async function handleSelectTrace(traceId: string) {
		// Use a local flag, NOT traceLoading/traceProgress — those represent an
		// in-flight coherence search and must keep reflecting it while the user
		// opens another trace from history. Clobbering them here is what caused
		// the in-flight card to disappear when the user clicked another query.
		selectingTrace = true;
		error = null;
		removedClusterIds = new Set();
		// Do NOT wipe narrative maps — in-flight streaming state for this trace (e.g. a
		// summary still generating after the user clicked Back) must survive re-selection.
		// Keys are scoped by trace_id so other traces' state doesn't collide.
		traceNewEventsDetected.set(false);

		try {
			const result = await engineApi.getTrace(traceId);
			trace = result;
			currentTrace.set(result);
			// Merge persisted narratives in without overwriting any in-flight streaming
			// content already captured from WebSocket messages.
			traceNarrativeMap.update((current) => {
				const merged = { ...current };
				for (const c of result.clusters) {
					const key = narrativeKey(result.trace_id, c.cluster_id);
					if (c.narrative && !merged[key]) merged[key] = c.narrative;
				}
				const sk = narrativeKey(result.trace_id, SUMMARY_ID);
				if (result.summary && !merged[sk]) merged[sk] = result.summary;
				return merged;
			});
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load trace';
		} finally {
			selectingTrace = false;
		}
	}

	async function handleRerun(traceId?: string) {
		const id = traceId || trace?.trace_id;
		if (!id) return;

		const rerunQuery = trace?.query ?? '';
		traceLoading.set(true);
		error = null;
		trace = null;
		currentTrace.set(null);
		removedClusterIds = new Set();
		traceNarrativeMap.set({});
		traceNarrativeStreamingMap.set({});
		traceNewEventsDetected.set(false);
		_activeTraceId = null;
		showHistoryDuringSearch = false;
		traceProgress.set({ stage: 'Initializing', step: 0, total: 6, query: rerunQuery });

		try {
			const result = await engineApi.rerunTrace(id);
			trace = result;
			currentTrace.set(result);
			loadHistory();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Rerun failed';
		} finally {
			traceLoading.set(false);
			traceProgress.set(null);
			showHistoryDuringSearch = false;
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

	// Overall summary state — uses __summary__ as a virtual cluster_id in the narrative maps
	const SUMMARY_ID = '__summary__';
	const summaryText = $derived(
		trace ? ($traceNarrativeMap[narrativeKey(trace.trace_id, SUMMARY_ID)] ?? '') : ''
	);
	const summaryStreaming = $derived(
		trace ? ($traceNarrativeStreamingMap[narrativeKey(trace.trace_id, SUMMARY_ID)] ?? false) : false
	);
	const hasSummary = $derived(!!summaryText || summaryStreaming);

	function parseLlmContent(content: string, streaming: boolean) {
		if (!content) return { thinking: null, response: content, isThinking: false };
		const match = content.match(/<think>([\s\S]*?)<\/think>/);
		if (match) {
			return {
				thinking: match[1].trim(),
				response: content.slice(match.index! + match[0].length).trim(),
				isThinking: false
			};
		}
		if (content.includes('<think>')) {
			if (streaming) {
				return { thinking: content.replace('<think>', '').trim(), response: '', isThinking: true };
			}
			return { thinking: null, response: content.replace('<think>', ''), isThinking: false };
		}
		return { thinking: null, response: content, isThinking: false };
	}

	const parsedSummary = $derived(parseLlmContent(summaryText, summaryStreaming));

	async function handleGenerateSummary() {
		if (!trace) return;
		try {
			await engineApi.generateTraceSummary(trace.trace_id);
		} catch (e) {
			console.error('Summary generation failed:', e);
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
		// During an in-flight search (no trace yet) Back should hide the progress view
		// and show Recent Searches without cancelling the running request — the search
		// continues in the background and surfaces as an in-flight card in the list.
		if (loading && !trace) {
			showHistoryDuringSearch = true;
			return;
		}
		trace = null;
		currentTrace.set(null);
		removedClusterIds = new Set();
		// Intentionally do NOT wipe traceNarrativeMap / traceNarrativeStreamingMap:
		// a summary may still be streaming for the trace we're leaving. Its keys are
		// scoped by trace_id, so keeping them lets the WebSocket handler continue to
		// accumulate chunks, and re-selecting the same trace shows the live spinner
		// and partial text rather than a misleadingly idle "Summarize" button.
		traceNewEventsDetected.set(false);
		// Preserve traceProgress while a coherence search is still in flight — the
		// Recent Searches in-flight card reads it to show the current stage/progress.
		// Nuking it here would blank the card until the next WebSocket tick.
		if (!loading) {
			traceProgress.set(null);
		}
		error = null;
	}

	function handleResumeSearchView() {
		showHistoryDuringSearch = false;
	}
</script>

{#if tooltip}
	<div
		class="fixed z-50 px-2 py-1 rounded-md border border-transparent glass-tooltip text-[10px] font-medium pointer-events-none -translate-x-1/2 -translate-y-full max-w-[400px] break-words"
		style="left: {tooltip.x}px; top: {tooltip.y}px;"
	>
		{tooltip.text}
	</div>
{/if}

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
			{#if trace || (loading && !showHistoryDuringSearch)}
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
					<span class="text-surface-600">·</span>
					<span class="flex items-center gap-1">
						{#if trace.search_metadata.enable_semantic !== false}
							<span class="px-1.5 py-0.5 rounded bg-laya-orange/10 text-laya-orange text-[9px] font-medium">Semantic</span>
						{/if}
						{#if trace.search_metadata.enable_text !== false}
							<span class="px-1.5 py-0.5 rounded bg-laya-gold/10 text-laya-gold text-[9px] font-medium">Text</span>
						{/if}
						{#if trace.search_metadata.enable_llm_filter !== false}
							<span class="px-1.5 py-0.5 rounded bg-laya-peach/10 text-laya-peach text-[9px] font-medium">AI Filter</span>
						{/if}
						{#if trace.search_metadata.fuzzy_search}
							<span class="px-1.5 py-0.5 rounded bg-laya-coral/10 text-laya-coral text-[9px] font-medium">Fuzzy</span>
						{/if}
					</span>
				</div>

				<div class="flex items-center gap-1.5">
					<button
						onclick={() => handleRerun()}
						onmouseenter={(e) => showTooltip(e, 'Re-run trace')}
						onmouseleave={hideTooltip}
						aria-label="Re-run trace"
						class="p-1 rounded text-surface-400 hover:text-laya-orange hover:bg-laya-orange/5 transition-colors"
					>
						<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
						</svg>
					</button>
					<button
						onclick={handleGenerateSummary}
						disabled={summaryStreaming}
						class="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium
						       text-surface-300 bg-surface-700/60 border border-surface-600/50
						       hover:border-laya-orange/40 hover:text-laya-orange hover:bg-laya-orange/5
						       disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
					>
						<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
						</svg>
						{summaryStreaming ? 'Summarizing...' : hasSummary ? 'Re-summarize' : 'Summarize'}
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

			<!-- Overall summary -->
			{#if hasSummary}
				<div class="mb-4 rounded-lg border border-laya-orange/20 bg-laya-orange/5 px-4 py-3">
					{#if parsedSummary.isThinking}
						<div class="flex items-center gap-1.5 text-[11px] text-surface-400">
							<svg class="h-2.5 w-2.5 animate-spin text-laya-orange/60 shrink-0" fill="none" viewBox="0 0 24 24">
								<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
								<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
							</svg>
							Generating summary...
						</div>
					{:else}
						<div class="flex items-center gap-1.5 mb-1.5">
							<span class="text-[9px] font-semibold uppercase tracking-wider text-laya-orange/70">✦ Summary</span>
							{#if summaryStreaming}
								<svg class="h-2.5 w-2.5 animate-spin text-laya-orange/60 shrink-0" fill="none" viewBox="0 0 24 24">
									<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
									<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
								</svg>
							{/if}
						</div>
						{#if parsedSummary.thinking}
							<details class="mb-1.5">
								<summary class="cursor-pointer text-[10px] text-surface-500 hover:text-surface-300 select-none list-none flex items-center gap-1">
									<svg class="w-2.5 h-2.5 shrink-0 transition-transform [[open]>&]:rotate-90" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
										<path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
									</svg>
									thought process
								</summary>
								<div class="ml-3.5 border-l border-laya-orange/10 pl-2 text-[10px] leading-relaxed text-surface-500 whitespace-pre-wrap mt-1">
									{parsedSummary.thinking}
								</div>
							</details>
						{/if}
						{#if parsedSummary.response}
							<p class="text-[12px] text-surface-200 leading-relaxed">
								{parsedSummary.response}{#if summaryStreaming && !parsedSummary.isThinking}<span class="inline-block w-1 h-3 bg-laya-orange/70 animate-pulse ml-0.5 align-middle"></span>{/if}
							</p>
						{/if}
					{/if}
				</div>
			{/if}

			<!-- Tree structure -->
			<div class="rounded-md border border-surface-700/50 bg-surface-800/30 px-4 py-2.5 overflow-hidden">
				<!-- Root node -->
				<div class="flex items-center gap-1.5 pb-2 mb-1 border-b border-surface-700/30">
					<span class="text-laya-orange text-[13px]">◆</span>
					<span class="text-[13px] font-medium text-surface-200">{trace.query}</span>
					<span class="text-[11px] text-surface-500 ml-1">{visibleClusters.length} clusters</span>

					<div class="ml-auto flex items-center gap-1.5">
						{#if clustersExpanded}
							<button
								onclick={() => { expandAllClusters = false; clustersExpanded = false; setTimeout(() => expandAllClusters = null, 50); }}
								class="text-[11px] text-surface-500 hover:text-laya-orange transition-colors"
							>collapse all</button>
						{:else}
							<button
								onclick={() => { expandAllClusters = true; clustersExpanded = true; setTimeout(() => expandAllClusters = null, 50); }}
								class="text-[11px] text-surface-500 hover:text-laya-orange transition-colors"
							>expand all</button>
						{/if}
						{#if removedClusterIds.size > 0}
							<span class="text-surface-600">·</span>
							<button
								onclick={handleRestoreClusters}
								class="text-[11px] text-surface-500 hover:text-laya-orange transition-colors"
							>
								restore {removedClusterIds.size}
							</button>
						{/if}
					</div>
				</div>

				<!-- Cluster nodes -->
				<div class="relative">
					{#each visibleClusters as cluster, idx (cluster.cluster_id)}
						<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }}>
							<TraceHeader
								{cluster}
								traceId={trace.trace_id}
								onremove={() => handleRemoveCluster(cluster.cluster_id)}
								ongenerate={() => handleGenerateNarrative(cluster.cluster_id)}
								isLast={idx === visibleClusters.length - 1}
								expandAll={expandAllClusters}
							/>
						</div>
					{/each}
				</div>
			</div>

		<!-- All clusters removed -->
		{:else if trace && trace.clusters.length > 0 && visibleClusters.length === 0}
			<div class="text-center py-10 text-surface-500" in:fade={{ duration: $reducedMotion ? 0 : 250, delay: $reducedMotion ? 0 : 300 }}>
				<p class="text-xs">All clusters removed.</p>
				<button
					onclick={handleRestoreClusters}
					class="mt-2 text-xs text-laya-orange hover:text-laya-gold transition-colors"
				>
					Restore all
				</button>
			</div>

		<!-- Empty state with trace history (also shown while an in-flight search is
		     backgrounded so the user sees the running query alongside recent ones) -->
		{:else if !trace && (!loading || showHistoryDuringSearch)}
			<div class="mt-4">
				<h2 class="text-xs font-medium text-surface-400 uppercase tracking-wider mb-3">
					Recent Searches
				</h2>

				{#if loading}
					<!-- In-flight search card: clicking re-opens the progress view -->
					<button
						type="button"
						onclick={handleResumeSearchView}
						class="w-full text-left rounded-lg border border-laya-orange/30 bg-laya-orange/5
						       hover:border-laya-orange/50 hover:bg-laya-orange/10 p-4 mb-2 transition-colors
						       cursor-pointer flex items-center gap-3"
					>
						<svg class="w-4 h-4 animate-spin shrink-0 text-laya-orange" fill="none" viewBox="0 0 24 24">
							<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3"></circle>
							<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
						</svg>
						<div class="flex-1 min-w-0">
							<div class="flex items-center gap-2">
								<h3 class="text-sm font-medium text-surface-100 truncate">
									"{$traceProgress?.query || 'Searching...'}"
								</h3>
								<span class="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium bg-laya-orange/15 text-laya-orange border border-laya-orange/30">
									Running
								</span>
							</div>
							<div class="mt-2 h-1 w-full rounded-full bg-surface-700/60 overflow-hidden">
								<div
									class="h-full rounded-full bg-gradient-to-r from-laya-orange to-laya-gold transition-all duration-500 ease-out"
									style="width: {$traceProgress ? Math.max(($traceProgress.step / $traceProgress.total) * 100, 8) : 8}%"
								></div>
							</div>
							<div class="flex items-center gap-2 mt-1.5 text-xs text-surface-400">
								<span>{($traceProgress?.step ?? 0) > 0 && ($traceProgress?.step ?? 0) <= coherenceStages.length ? coherenceStages[($traceProgress?.step ?? 1) - 1] : 'Preparing'}</span>
								<span class="text-surface-600">&middot;</span>
								<span class="text-surface-500">{$traceProgress?.step ?? 0} / {$traceProgress?.total ?? coherenceStages.length}</span>
							</div>
						</div>
						<svg class="w-4 h-4 text-surface-500 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
						</svg>
					</button>
				{/if}

				<TraceHistory
					traces={$traceHistory}
					onselect={handleSelectTrace}
					ondelete={handleDelete}
					onrerun={handleRerun}
				/>
			</div>
		{/if}

		<!-- Loading progress -->
		{#if loading && !trace && !showHistoryDuringSearch}
			<div class="mt-6 rounded-xl border border-surface-700/50 bg-surface-800/40 p-6" in:fade={{ duration: $reducedMotion ? 0 : 200 }}>
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

				<!-- Current stage + cancel -->
				<div class="flex items-center justify-between">
					<div class="flex items-center gap-2 text-xs text-surface-400">
						<svg class="w-3.5 h-3.5 animate-spin shrink-0 text-laya-orange" fill="none" viewBox="0 0 24 24">
							<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3"></circle>
							<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
						</svg>
						<span class="font-medium transition-all duration-300">{($traceProgress?.step ?? 0) > 0 && ($traceProgress?.step ?? 0) <= coherenceStages.length ? coherenceStages[($traceProgress?.step ?? 1) - 1] : 'Preparing'}</span>
						<span class="text-surface-600">&middot;</span>
						<span class="text-surface-500">{$traceProgress?.step ?? 0} / {$traceProgress?.total ?? coherenceStages.length}</span>
					</div>
					<button
						onclick={handleCancelSearch}
						disabled={cancelling}
						class="text-xs transition-colors {cancelling ? 'text-surface-600 cursor-not-allowed' : 'text-surface-500 hover:text-red-400'}"
					>
						{cancelling ? 'Cancelling...' : 'Cancel'}
					</button>
				</div>
			</div>
		{/if}

	</div>
</div>
