<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { slide } from 'svelte/transition';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { setAuditFailureCounts } from '$lib/stores/auditFailures';
	import { portal } from '$lib/actions/portal';
	import Dropdown from '$lib/components/Dropdown.svelte';
	import ExportMenu from '$lib/components/settings/ExportMenu.svelte';
	import type { AuditLogEntry, DeadEvent, IngestionError, FilteredEvent } from '$lib/api/types';

	// Ordered list of processing_status values for stable display order.
	// Mirrors values set in engine/laya/pipeline/queue.py and api/events.py.
	const EVENT_STATUSES = ['queued', 'processing', 'retrying', 'completed', 'filtered', 'dead'] as const;

	// ── Event counts state (polls /events/counts while mounted) ──
	let eventCounts = $state<Record<string, number>>({});
	let eventCountsTotal = $state(0);
	let eventCountsLoaded = $state(false);
	let eventCountsPollId: ReturnType<typeof setInterval> | null = null;

	async function loadEventCounts() {
		try {
			const resp = await engineApi.getEventCounts();
			eventCounts = resp.counts;
			eventCountsTotal = resp.total;
			eventCountsLoaded = true;
		} catch {
			// Silent — supplementary section
		}
	}

	const AUDIT_STEPS = [
		'route', 'route_batch', 'stage', 'emit', 'worker',
		'entity_confirm', 'context_confirm', 'context_learn', 'learn',
		'chat', 'chat_title',
		'summarize', 'group_summary_initial', 'group_summary_rolling',
		'trace', 'trace_filter', 'trace_summary',
		'omni_resynthesis',
		'execute', 'lifecycle', 'recovery',
		'briefing',
		'egress_draft', 'compose_polish', 'polish_draft',
		'settings',
	];

	// ── Ingestion errors state ──
	let ingestionErrors: IngestionError[] = $state([]);
	let ingestionLoading = $state(true);
	let ingestionExpanded = $state(false);
	let clearingIngestion = $state<Set<string>>(new Set());
	let dismissingIngestion = $state<Set<string>>(new Set());
	let clearingAllIngestion = $state(false);
	let clearAllIngestionConfirm = $state(false);

	async function loadIngestionErrors() {
		ingestionLoading = true;
		try {
			const resp = await engineApi.getIngestionErrors({ limit: 100 });
			ingestionErrors = resp.errors;
		} catch {
			// Silent — supplementary section
		} finally {
			ingestionLoading = false;
		}
	}

	async function clearIngestionError(errorId: string) {
		clearingIngestion = new Set([...clearingIngestion, errorId]);
		try {
			await engineApi.clearIngestionError(errorId);
			dismissingIngestion = new Set([...dismissingIngestion, errorId]);
			const next = new Set(clearingIngestion);
			next.delete(errorId);
			clearingIngestion = next;
			setTimeout(() => {
				ingestionErrors = ingestionErrors.filter(e => e.error_id !== errorId);
				const d = new Set(dismissingIngestion);
				d.delete(errorId);
				dismissingIngestion = d;
			}, 350);
		} catch {
			const next = new Set(clearingIngestion);
			next.delete(errorId);
			clearingIngestion = next;
		}
	}

	async function clearAllIngestion() {
		if (!clearAllIngestionConfirm) {
			clearAllIngestionConfirm = true;
			return;
		}
		clearingAllIngestion = true;
		clearAllIngestionConfirm = false;
		try {
			await engineApi.clearAllIngestionErrors();
			dismissingIngestion = new Set(ingestionErrors.map(e => e.error_id));
			setTimeout(() => {
				ingestionErrors = [];
				dismissingIngestion = new Set();
				ingestionExpanded = false;
			}, 350);
		} finally {
			clearingAllIngestion = false;
		}
	}

	// ── Audit log state ──
	let entries: AuditLogEntry[] = $state([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state('');

	let filterSteps = $state<Set<string>>(new Set());
	let filterSuccess = $state<'' | 'true' | 'false'>('');
	let searchQuery = $state('');
	let limit = 25;
	let offset = $state(0);

	let stepsOpen = $state(false);
	let stepsTriggerRef = $state<HTMLElement | null>(null);
	let stepsPanelRef = $state<HTMLDivElement | null>(null);
	let stepsDropPos = $state({ top: 0, left: 0, width: 0, openUp: false });

	let stepsLabel = $derived(
		filterSteps.size === 0
			? 'All steps'
			: filterSteps.size === 1
				? [...filterSteps][0]
				: `${filterSteps.size} steps`
	);

	function positionStepsPanel() {
		if (!stepsTriggerRef) return;
		const r = stepsTriggerRef.getBoundingClientRect();
		const spaceBelow = window.innerHeight - r.bottom;
		const panelMaxH = 320;
		const openUp = spaceBelow < panelMaxH && r.top > spaceBelow;
		stepsDropPos = {
			top: openUp ? r.top - 4 : r.bottom + 4,
			left: r.left,
			width: Math.max(r.width, 220),
			openUp
		};
	}

	function toggleStepsDropdown() {
		if (stepsOpen) { stepsOpen = false; return; }
		positionStepsPanel();
		stepsOpen = true;
	}

	function toggleStep(step: string) {
		const next = new Set(filterSteps);
		if (next.has(step)) next.delete(step); else next.add(step);
		filterSteps = next;
	}

	function handleStepsWindowClick(e: MouseEvent) {
		if (!stepsOpen) return;
		const target = e.target as Node;
		if (stepsTriggerRef?.contains(target)) return;
		if (stepsPanelRef?.contains(target)) return;
		stepsOpen = false;
	}

	// ── Dead events state ──
	let deadEvents: DeadEvent[] = $state([]);
	let deadTotal = $state(0);
	let deadLoading = $state(true);
	let deadExpanded = $state(false);
	let retrying = $state<Set<string>>(new Set());
	let dismissing = $state<Set<string>>(new Set());
	let retryingAll = $state(false);
	let retryAllConfirm = $state(false);

	async function loadDeadEvents() {
		deadLoading = true;
		try {
			const resp = await engineApi.getDeadEvents({ limit: 50 });
			deadEvents = resp.events;
			deadTotal = resp.total;
		} catch {
			// Silent — dead events section is supplementary
		} finally {
			deadLoading = false;
		}
	}

	async function retryOne(eventId: string) {
		retrying = new Set([...retrying, eventId]);
		try {
			await engineApi.retryDeadEvents([eventId]);
			// Optimistic fade-out: mark as dismissing, then remove after animation
			dismissing = new Set([...dismissing, eventId]);
			const next = new Set(retrying);
			next.delete(eventId);
			retrying = next;
			setTimeout(() => {
				deadEvents = deadEvents.filter(e => e.event_id !== eventId);
				deadTotal = Math.max(0, deadTotal - 1);
				const d = new Set(dismissing);
				d.delete(eventId);
				dismissing = d;
			}, 350);
		} catch {
			const next = new Set(retrying);
			next.delete(eventId);
			retrying = next;
		}
	}

	async function retryAll() {
		if (!retryAllConfirm) {
			retryAllConfirm = true;
			return;
		}
		retryingAll = true;
		retryAllConfirm = false;
		try {
			await engineApi.retryDeadEvents();
			// Fade out all rows then clear
			dismissing = new Set(deadEvents.map(e => e.event_id));
			setTimeout(() => {
				deadEvents = [];
				deadTotal = 0;
				dismissing = new Set();
			}, 350);
		} finally {
			retryingAll = false;
		}
	}

	// ── Filtered events state ──
	// Purely informational: events dropped by filter rules. Deliberately NOT
	// fed into setAuditFailureCounts — filtered events are not failures and
	// must never light up the audit/settings red-dot indicator.
	const FILTERED_LIMIT = 25;
	let filteredEvents: FilteredEvent[] = $state([]);
	let filteredTotal = $state(0);
	let filteredLoading = $state(true);
	let filteredExpanded = $state(false);
	let filteredOffset = $state(0);

	async function loadFilteredEvents() {
		filteredLoading = true;
		try {
			const resp = await engineApi.getFilteredEvents({ limit: FILTERED_LIMIT, offset: filteredOffset });
			filteredEvents = resp.events;
			filteredTotal = resp.total;
		} catch {
			// Silent — supplementary section
		} finally {
			filteredLoading = false;
		}
	}

	function filteredPrevPage() {
		if (filteredOffset >= FILTERED_LIMIT) {
			filteredOffset -= FILTERED_LIMIT;
			loadFilteredEvents();
		}
	}

	function filteredNextPage() {
		if (filteredOffset + FILTERED_LIMIT < filteredTotal) {
			filteredOffset += FILTERED_LIMIT;
			loadFilteredEvents();
		}
	}

	const filteredPage = $derived(Math.floor(filteredOffset / FILTERED_LIMIT) + 1);
	const filteredTotalPages = $derived(Math.ceil(filteredTotal / FILTERED_LIMIT) || 1);

	// ── Export ──
	// Triggers a JSON file download from an already-fetched object. Pretty-printed
	// so the export is human-inspectable. Filename carries the timeframe + date.
	function downloadJson(data: unknown, base: string) {
		const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `${base}-${new Date().toISOString().slice(0, 10)}.json`;
		a.click();
		URL.revokeObjectURL(url);
	}

	function timeframeSlug(days: number): string {
		return days > 0 ? `${days}d` : 'all';
	}

	async function exportFiltered(days: number) {
		const data = await engineApi.exportFilteredEvents(days);
		downloadJson(data, `laya-filtered-events-${timeframeSlug(days)}`);
	}

	async function exportAudit(days: number) {
		// Mirror the current view's filters so the export matches what's on screen.
		const params: { days: number; step?: string; success?: boolean; search?: string } = { days };
		if (filterSteps.size > 0) params.step = [...filterSteps].join(',');
		if (filterSuccess !== '') params.success = filterSuccess === 'true';
		if (searchQuery.trim()) params.search = searchQuery.trim();
		const data = await engineApi.exportAuditLog(params);
		downloadJson(data, `laya-audit-log-${timeframeSlug(days)}`);
	}

	// ── Audit log functions ──
	async function load() {
		loading = true;
		error = '';
		try {
			const params: Record<string, unknown> = { limit, offset };
			if (filterSteps.size > 0) params.step = [...filterSteps].join(',');
			if (filterSuccess !== '') params.success = filterSuccess === 'true';
			if (searchQuery.trim()) params.search = searchQuery.trim();
			const resp = await engineApi.getAuditLog(params as Parameters<typeof engineApi.getAuditLog>[0]);
			entries = resp.entries;
			total = resp.total;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load audit log';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		load();
		loadDeadEvents();
		loadIngestionErrors();
		loadFilteredEvents();
		// Event counts: poll every 10s while the Audit page is mounted.
		// onDestroy clears the interval when the user leaves the tab so
		// the DB isn't queried in the background.
		loadEventCounts();
		eventCountsPollId = setInterval(loadEventCounts, 10000);
	});

	onDestroy(() => {
		if (eventCountsPollId !== null) {
			clearInterval(eventCountsPollId);
			eventCountsPollId = null;
		}
	});

	// Re-seed the global red-dot indicator from this panel's authoritative state.
	// This is what makes both dots (Audit tab + Settings nav) disappear the moment
	// the user retries every dead event and clears every ingestion error. Guarded on
	// load completion so the dot doesn't flicker off at mount before the data lands.
	$effect(() => {
		if (deadLoading || ingestionLoading) return;
		setAuditFailureCounts(deadTotal, ingestionErrors.length);
	});

	function applyFilter() {
		offset = 0;
		load();
	}

	function prevPage() {
		if (offset >= limit) {
			offset -= limit;
			load();
		}
	}

	function nextPage() {
		if (offset + limit < total) {
			offset += limit;
			load();
		}
	}

	const page = $derived(Math.floor(offset / limit) + 1);
	const totalPages = $derived(Math.ceil(total / limit) || 1);

	function formatTime(ts: string): string {
		const utc = ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z';
		return new Date(utc).toLocaleString([], {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit',
			second: '2-digit',
			hour12: false // force 24-hour clock regardless of locale (en-US would otherwise show AM/PM)
		});
	}

	function formatTokens(n: number): string {
		if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
		return String(n);
	}

	function truncateError(err: string | undefined, maxLen: number = 80): string {
		if (!err) return '-';
		return err.length > maxLen ? err.slice(0, maxLen) + '...' : err;
	}

	// ── Portal tooltip ──
	let fixedTooltip = $state<{ text: string; top: number; left?: number; right?: number; maxWidth: number; color?: string } | null>(null);

	function showTooltip(el: HTMLElement, text: string, opts?: { maxWidth?: number; color?: string }) {
		const rect = el.getBoundingClientRect();
		const useRight = rect.left > window.innerWidth / 2;
		fixedTooltip = {
			text, top: rect.bottom + 4, maxWidth: opts?.maxWidth ?? 320, color: opts?.color,
			...(useRight ? { right: window.innerWidth - rect.right } : { left: rect.left })
		};
	}

	function hideTooltip() { fixedTooltip = null; }
</script>

<svelte:window onclick={handleStepsWindowClick} />

<div class="space-y-4">
	<!-- Event Counts by processing_status (live, polled every 10s) -->
	<div class="rounded-lg border {$glassTheme ? 'glass-section border-white/[0.06]' : 'border-surface-700 bg-surface-900/40'} px-3 py-2 text-laya-secondary">
		<div class="flex flex-wrap items-center gap-x-4 gap-y-1">
			<span class="font-medium text-surface-300">Events</span>
			<span class="text-surface-400">
				<span class="font-medium text-surface-200">{eventCountsLoaded ? eventCountsTotal.toLocaleString() : '—'}</span>
				total
			</span>
			<span class="h-3 w-px bg-surface-700"></span>
			{#each EVENT_STATUSES as status}
				<span class="text-surface-400">
					<span class="font-medium text-surface-200">{eventCountsLoaded ? (eventCounts[status] ?? 0).toLocaleString() : '—'}</span>
					{status}
				</span>
			{/each}
		</div>
	</div>

	<!-- Dead Events Recovery -->
	{#if !deadLoading && deadTotal > 0}
		<div class="rounded-lg border border-amber-600/50 bg-amber-900/20 p-3">
			<div class="flex items-center justify-between">
				<div class="flex items-center gap-2">
					<svg class="h-4 w-4 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
					</svg>
					<span class="text-laya-base font-medium text-amber-300">
						{deadTotal} event{deadTotal !== 1 ? 's' : ''} failed permanently
					</span>
				</div>
				<div class="flex items-center gap-2">
					<button
						onclick={() => { deadExpanded = !deadExpanded; retryAllConfirm = false; }}
						class="rounded px-3 py-1 text-laya-secondary font-medium text-surface-300 transition-colors {$glassTheme ? 'hover:bg-white/[0.08]' : 'hover:bg-surface-700'}"
					>
						{deadExpanded ? 'Hide' : 'View'}
					</button>
					<button
						onclick={retryAll}
						disabled={retryingAll}
						class="rounded bg-laya-orange/20 px-3 py-1 text-laya-secondary font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:opacity-50"
					>
						{#if retryingAll}
							Retrying...
						{:else if retryAllConfirm}
							Confirm retry all?
						{:else}
							Retry All
						{/if}
					</button>
				</div>
			</div>

			{#if deadExpanded}
				<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }} class="mt-3 overflow-visible {$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700'}">
					<table class="w-full table-fixed text-left text-laya-secondary">
						<thead class="border-b {$glassTheme ? 'border-white/[0.06] bg-white/[0.03]' : 'border-surface-700 bg-surface-800'} text-surface-400">
							<tr>
								<th class="w-[18%] px-3 py-2">Time</th>
								<th class="w-[10%] px-3 py-2">Platform</th>
								<th class="w-[22%] px-3 py-2">Subject</th>
								<th class="w-[24%] px-3 py-2">Error</th>
								<th class="w-[16%] px-3 py-2">Attempts</th>
								<th class="w-[10%] px-3 py-2">Action</th>
							</tr>
						</thead>
						<tbody class="divide-y {$glassTheme ? 'divide-white/[0.04]' : 'divide-surface-700/50'}">
							{#each deadEvents as evt (evt.event_id)}
								<tr class="transition-all duration-300 ease-out hover:bg-surface-800/50 {dismissing.has(evt.event_id) ? 'opacity-0 translate-x-4' : 'opacity-100 translate-x-0'}">
									<td class="px-3 py-2 text-surface-300">
										<span class="block truncate">{formatTime(evt.created_at)}</span>
									</td>
									<td class="px-3 py-2">
										<span
											class="inline-block max-w-full truncate rounded bg-surface-700 px-1.5 py-0.5 text-surface-300"
											role="note"
											onmouseenter={(e) => { const el = e.currentTarget; if (el.scrollWidth > el.clientWidth) showTooltip(el, evt.source_platform); }}
											onmouseleave={hideTooltip}
										>
											{evt.source_platform}
										</span>
									</td>
									<td
										class="px-3 py-2 font-medium text-surface-200"
										onmouseenter={(e) => { if (evt.subject_title && evt.subject_title.length > 25) showTooltip(e.currentTarget, evt.subject_title); }}
										onmouseleave={hideTooltip}
									>
										<span class="block truncate">
											{#if evt.subject_url}
												<a href={evt.subject_url} target="_blank" rel="noopener" class="hover:text-laya-orange hover:underline">
													{evt.subject_title}
												</a>
											{:else}
												{evt.subject_title}
											{/if}
										</span>
									</td>
									<td
										class="px-3 py-2 text-red-400"
										onmouseenter={(e) => { if (evt.last_error && evt.last_error.length > 30) showTooltip(e.currentTarget, evt.last_error, { maxWidth: 400, color: 'text-red-300' }); }}
										onmouseleave={hideTooltip}
									>
										<span class="block truncate">
											{truncateError(evt.last_error)}
										</span>
									</td>
									<td class="px-3 py-2 text-surface-400">
										<span class="block truncate">{evt.processing_attempts} attempt{evt.processing_attempts !== 1 ? 's' : ''}{#if evt.manual_retries > 0}, retried {evt.manual_retries}x{/if}</span>
									</td>
									<td class="px-3 py-2">
										<button
											onclick={() => retryOne(evt.event_id)}
											disabled={retrying.has(evt.event_id)}
											class="rounded bg-laya-orange/20 px-2 py-0.5 text-laya-secondary font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:opacity-50"
										>
											{retrying.has(evt.event_id) ? 'Retrying...' : 'Retry'}
										</button>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</div>
	{/if}

	<!-- Ingestion Errors -->
	{#if !ingestionLoading && ingestionErrors.length > 0}
		<div class="rounded-lg border border-red-600/50 bg-red-900/20 p-3">
			<div class="flex items-center justify-between">
				<div class="flex items-center gap-2">
					<svg class="h-4 w-4 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
					</svg>
					<span class="text-laya-base font-medium text-red-300">
						{ingestionErrors.length} ingestion error{ingestionErrors.length !== 1 ? 's' : ''} detected
					</span>
				</div>
				<div class="flex items-center gap-2">
					<button
						onclick={() => { ingestionExpanded = !ingestionExpanded; clearAllIngestionConfirm = false; }}
						class="rounded px-3 py-1 text-laya-secondary font-medium text-surface-300 transition-colors {$glassTheme ? 'hover:bg-white/[0.08]' : 'hover:bg-surface-700'}"
					>
						{ingestionExpanded ? 'Hide' : 'View'}
					</button>
					<button
						onclick={clearAllIngestion}
						disabled={clearingAllIngestion}
						class="rounded bg-red-500/20 px-3 py-1 text-laya-secondary font-medium text-red-300 transition-colors hover:bg-red-500/30 disabled:opacity-50"
					>
						{#if clearingAllIngestion}
							Clearing...
						{:else if clearAllIngestionConfirm}
							Confirm clear all?
						{:else}
							Clear All
						{/if}
					</button>
				</div>
			</div>

			{#if ingestionExpanded}
				<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }} class="mt-3 overflow-visible {$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700'}">
					<table class="w-full table-fixed text-left text-laya-secondary">
						<thead class="border-b {$glassTheme ? 'border-white/[0.06] bg-white/[0.03]' : 'border-surface-700 bg-surface-800'} text-surface-400">
							<tr>
								<th class="w-[15%] px-3 py-2">Time</th>
								<th class="w-[9%] px-3 py-2">Platform</th>
								<th class="w-[15%] px-3 py-2">Workflow</th>
								<th class="w-[13%] px-3 py-2">Node</th>
								<th class="w-[28%] px-3 py-2">Error</th>
								<th class="w-[8%] px-3 py-2">Count</th>
								<th class="w-[12%] px-3 py-2">Action</th>
							</tr>
						</thead>
						<tbody class="divide-y {$glassTheme ? 'divide-white/[0.04]' : 'divide-surface-700/50'}">
							{#each ingestionErrors as err (err.error_id)}
								<tr class="transition-all duration-300 ease-out hover:bg-surface-800/50 {dismissingIngestion.has(err.error_id) ? 'opacity-0 translate-x-4' : 'opacity-100 translate-x-0'}">
									<td class="px-3 py-2 text-surface-300">
										<span class="block truncate">{formatTime(err.last_occurred_at)}</span>
									</td>
									<td class="px-3 py-2">
										{#if err.platform}
											<span
												class="inline-block max-w-full truncate rounded bg-surface-700 px-1.5 py-0.5 text-surface-300"
												onmouseenter={(e) => { const el = e.currentTarget; if (el.scrollWidth > el.clientWidth) showTooltip(el, err.platform!); }}
												role="note"
												onmouseleave={hideTooltip}
											>
												{err.platform}
											</span>
										{:else}
											<span class="text-surface-500">-</span>
										{/if}
									</td>
									<td
										class="px-3 py-2 text-surface-300"
										onmouseenter={(e) => { if (err.workflow_name && err.workflow_name.length > 18) showTooltip(e.currentTarget, err.workflow_name); }}
										onmouseleave={hideTooltip}
									>
										<span class="block truncate">{err.workflow_name ?? '-'}</span>
									</td>
									<td
										class="px-3 py-2 text-surface-400"
										onmouseenter={(e) => { if (err.node_name && err.node_name.length > 14) showTooltip(e.currentTarget, err.node_name); }}
										onmouseleave={hideTooltip}
									>
										<span class="block truncate">{err.node_name ?? '-'}</span>
									</td>
									<td
										class="px-3 py-2 text-red-400"
										onmouseenter={(e) => { if (err.error_message && err.error_message.length > 32) showTooltip(e.currentTarget, err.error_message, { maxWidth: 400, color: 'text-red-300' }); }}
										onmouseleave={hideTooltip}
									>
										<span class="block truncate">
											{truncateError(err.error_message)}
										</span>
									</td>
									<td class="px-3 py-2 text-surface-400">
										{err.occurrence_count}x
									</td>
									<td class="px-3 py-2">
										<button
											onclick={() => clearIngestionError(err.error_id)}
											disabled={clearingIngestion.has(err.error_id)}
											class="rounded bg-red-500/20 px-2 py-0.5 text-laya-secondary font-medium text-red-300 transition-colors hover:bg-red-500/30 disabled:opacity-50"
										>
											{clearingIngestion.has(err.error_id) ? 'Clearing...' : 'Clear'}
										</button>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</div>
	{/if}

	<!-- Filtered Events (informational — events dropped by filter rules; NOT failures) -->
	{#if !filteredLoading && filteredTotal > 0}
		<div class="rounded-lg border border-blue-600/50 bg-blue-900/20 p-3">
			<div class="flex items-center justify-between">
				<div class="flex items-center gap-2">
					<svg class="h-4 w-4 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2a1 1 0 01-.293.707L15 12.414V19a1 1 0 01-.553.894l-4 2A1 1 0 019 21v-8.586L3.293 6.707A1 1 0 013 6V4z" />
					</svg>
					<span class="text-laya-base font-medium text-blue-300">
						{filteredTotal.toLocaleString()} event{filteredTotal !== 1 ? 's' : ''} filtered
					</span>
				</div>
				<div class="flex items-center gap-1">
					<button
						onclick={() => { filteredExpanded = !filteredExpanded; }}
						class="rounded px-3 py-1 text-laya-secondary font-medium text-surface-300 transition-colors {$glassTheme ? 'hover:bg-white/[0.08]' : 'hover:bg-surface-700'}"
					>
						{filteredExpanded ? 'Hide' : 'View'}
					</button>
					<ExportMenu onexport={exportFiltered} />
				</div>
			</div>

			{#if filteredExpanded}
				<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }} class="mt-3">
					<div class="overflow-visible {$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700'}">
						<table class="w-full table-fixed text-left text-laya-secondary">
							<thead class="border-b {$glassTheme ? 'border-white/[0.06] bg-white/[0.03]' : 'border-surface-700 bg-surface-800'} text-surface-400">
								<tr>
									<th class="w-[20%] px-3 py-2">Time</th>
									<th class="w-[12%] px-3 py-2">Platform</th>
									<th class="w-[33%] px-3 py-2">Subject</th>
									<th class="w-[15%] px-3 py-2">Actor</th>
									<th class="w-[20%] px-3 py-2">Rule</th>
								</tr>
							</thead>
							<tbody class="divide-y {$glassTheme ? 'divide-white/[0.04]' : 'divide-surface-700/50'}">
								{#each filteredEvents as evt (evt.event_id)}
									<tr class="transition-colors hover:bg-surface-800/50">
										<td class="px-3 py-2 text-surface-300">
											<span class="block truncate">{formatTime(evt.created_at)}</span>
										</td>
										<td class="px-3 py-2">
											<span
												class="inline-block max-w-full truncate rounded bg-surface-700 px-1.5 py-0.5 text-surface-300"
												role="note"
												onmouseenter={(e) => { const el = e.currentTarget; if (el.scrollWidth > el.clientWidth) showTooltip(el, evt.source_platform); }}
												onmouseleave={hideTooltip}
											>
												{evt.source_platform}
											</span>
										</td>
										<td
											class="px-3 py-2 font-medium text-surface-200"
											onmouseenter={(e) => { if (evt.subject_title && evt.subject_title.length > 40) showTooltip(e.currentTarget, evt.subject_title); }}
											onmouseleave={hideTooltip}
										>
											<span class="block truncate">
												{#if evt.subject_url}
													<a href={evt.subject_url} target="_blank" rel="noopener" class="hover:text-laya-orange hover:underline">
														{evt.subject_title ?? '(untitled)'}
													</a>
												{:else}
													{evt.subject_title ?? '(untitled)'}
												{/if}
											</span>
										</td>
										<td
											class="px-3 py-2 text-surface-400"
											onmouseenter={(e) => { if (evt.actor_name && evt.actor_name.length > 18) showTooltip(e.currentTarget, evt.actor_name); }}
											onmouseleave={hideTooltip}
										>
											<span class="block truncate">{evt.actor_name ?? '-'}</span>
										</td>
										<td
											class="px-3 py-2 text-surface-400"
											onmouseenter={(e) => { if (evt.filter_rule && evt.filter_rule.length > 24) showTooltip(e.currentTarget, evt.filter_rule); }}
											onmouseleave={hideTooltip}
										>
											<span class="block truncate">{evt.filter_rule ?? '-'}</span>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>

					<!-- Filtered events pagination -->
					<div class="mt-2 flex items-center justify-between text-laya-secondary text-surface-400">
						<span>{filteredTotal.toLocaleString()} filtered</span>
						<div class="flex items-center gap-2">
							<button
								onclick={filteredPrevPage}
								disabled={filteredOffset === 0}
								class="rounded px-2 py-1 transition-colors hover:bg-surface-700 disabled:opacity-30"
							>
								Prev
							</button>
							<span>{filteredPage} / {filteredTotalPages}</span>
							<button
								onclick={filteredNextPage}
								disabled={filteredOffset + FILTERED_LIMIT >= filteredTotal}
								class="rounded px-2 py-1 transition-colors hover:bg-surface-700 disabled:opacity-30"
							>
								Next
							</button>
						</div>
					</div>
				</div>
			{/if}
		</div>
	{/if}

	<!-- Filters -->
	<div class="flex flex-wrap items-end gap-3">
		<!-- Steps multiselect dropdown -->
		<div class="relative">
			<span class="mb-1 block text-laya-secondary text-surface-400">Steps</span>
			<button
				bind:this={stepsTriggerRef}
				type="button"
				onclick={toggleStepsDropdown}
				class="flex h-[38px] items-center justify-between gap-2 rounded-md border px-3 text-left text-sm transition-colors
					{$glassTheme
						? 'border-surface-600/40 bg-surface-800/40 backdrop-blur-sm text-surface-200 hover:border-surface-500/50'
						: 'border-surface-600 bg-surface-900 text-surface-200 hover:border-surface-500'}"
				style="min-width: 160px"
				aria-haspopup="listbox"
				aria-expanded={stepsOpen}
			>
				<span class="truncate {filterSteps.size === 0 ? 'text-surface-400' : ''}">{stepsLabel}</span>
				{#if filterSteps.size > 0}
					<span class="flex h-4 min-w-4 items-center justify-center rounded-full bg-laya-orange/20 px-1 text-[10px] font-semibold text-laya-orange">{filterSteps.size}</span>
				{/if}
				<svg
					class="h-3.5 w-3.5 shrink-0 text-surface-400 transition-transform {stepsOpen ? 'rotate-180' : ''}"
					fill="none" stroke="currentColor" viewBox="0 0 24 24"
				>
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
				</svg>
			</button>
			{#if stepsOpen}
				<div
					use:portal
					bind:this={stepsPanelRef}
					class="fixed z-[100] rounded-lg border p-1 overflow-hidden
						{$glassTheme
							? 'glass-dropdown border-white/15'
							: 'border-surface-600 bg-surface-800 shadow-xl shadow-black/30'}"
					style="
						{stepsDropPos.openUp ? `bottom: ${window.innerHeight - stepsDropPos.top}px` : `top: ${stepsDropPos.top}px`};
						left: {stepsDropPos.left}px;
						min-width: {stepsDropPos.width}px;
						max-width: min(280px, calc(100vw - 32px));
					"
					role="listbox"
					aria-multiselectable="true"
				>
					<div class="max-h-72 overflow-y-auto">
						{#each AUDIT_STEPS as step}
							{@const selected = filterSteps.has(step)}
							<button
								type="button"
								role="option"
								aria-selected={selected}
								onclick={() => toggleStep(step)}
								class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm transition-colors
									{selected
										? ($glassTheme
											? 'bg-white/[0.14] text-surface-100 font-medium'
											: 'bg-surface-600 text-surface-100 font-medium')
										: ($glassTheme
											? 'text-surface-300 hover:bg-white/[0.06]'
											: 'text-surface-300 hover:bg-surface-700')}"
							>
								<span class="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border transition-colors
									{selected
										? 'border-laya-orange bg-laya-orange/20'
										: ($glassTheme ? 'border-surface-500/50' : 'border-surface-500')}">
									{#if selected}
										<svg class="h-2.5 w-2.5 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
											<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
										</svg>
									{/if}
								</span>
								<span class="truncate">{step}</span>
							</button>
						{/each}
					</div>
				</div>
			{/if}
		</div>

		<div class="w-28">
			<span class="mb-1 block text-laya-secondary text-surface-400">Status</span>
			<Dropdown
				bind:value={filterSuccess}
				options={[
					{ value: '', label: 'All' },
					{ value: 'true', label: 'Success' },
					{ value: 'false', label: 'Failed' },
				]}
				onchange={(v) => { filterSuccess = v as typeof filterSuccess; }}
			/>
		</div>
		<button
			onclick={applyFilter}
			class="h-[38px] rounded-lg px-4 text-laya-base font-medium text-surface-200 transition-colors {$glassTheme ? 'bg-white/[0.08] hover:bg-white/[0.14]' : 'bg-surface-700 hover:bg-surface-600'}"
		>
			Apply
		</button>
		{#if filterSteps.size > 0 || filterSuccess !== '' || searchQuery.trim()}
			<button
				onclick={() => { filterSteps = new Set(); filterSuccess = ''; searchQuery = ''; applyFilter(); }}
				class="h-[38px] rounded-lg px-3 text-laya-base text-surface-400 transition-colors hover:text-surface-200"
			>
				Clear
			</button>
		{/if}

		<!-- Search box — right-aligned -->
		<div class="relative ml-auto w-64">
			<svg class="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-surface-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
			</svg>
			<input
				type="text"
				bind:value={searchQuery}
				placeholder="Search logs…"
				onkeydown={(e) => { if (e.key === 'Enter') applyFilter(); }}
				class="h-[38px] w-full rounded-md border pl-8 pr-3 text-laya-secondary text-surface-200 placeholder-surface-500 focus:outline-none
					{$glassTheme ? 'glass-input focus:border-laya-orange/50' : 'border-surface-600 bg-surface-900 focus:border-laya-orange/50'}"
			/>
		</div>
	</div>

	{#if loading}
		<div class="flex justify-center py-8">
			<div class="h-5 w-5 animate-spin rounded-full border-2 border-blue-400 border-t-transparent"></div>
		</div>
	{:else if error}
		<div class="rounded-lg border border-red-800 bg-red-900/20 p-3 text-laya-base text-red-300">{error}</div>
	{:else if entries.length === 0}
		<p class="py-8 text-center text-laya-base text-surface-500">No audit log entries found.</p>
	{:else}
		<!-- Pagination + export -->
		<div class="flex items-center justify-between text-laya-secondary text-surface-400">
			<span>{total} entries</span>
			<div class="flex items-center gap-3">
				<ExportMenu onexport={exportAudit} />
				<div class="flex items-center gap-2">
					<button
						onclick={prevPage}
						disabled={offset === 0}
						class="rounded px-2 py-1 transition-colors hover:bg-surface-700 disabled:opacity-30"
					>
						Prev
					</button>
					<span>{page} / {totalPages}</span>
					<button
						onclick={nextPage}
						disabled={offset + limit >= total}
						class="rounded px-2 py-1 transition-colors hover:bg-surface-700 disabled:opacity-30"
					>
						Next
					</button>
				</div>
			</div>
		</div>

		<div class="overflow-x-auto {$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700'}">
			<table class="w-full text-left text-laya-secondary">
				<thead class="border-b {$glassTheme ? 'border-white/[0.06] bg-white/[0.03]' : 'border-surface-700 bg-surface-800'} text-surface-400">
					<tr>
						<th class="px-3 py-2">Time</th>
						<th class="px-3 py-2">Step</th>
						<th class="px-3 py-2">Model</th>
						<th class="px-3 py-2">Tokens</th>
						<th class="px-3 py-2">Latency</th>
						<th class="px-3 py-2">Status</th>
					</tr>
				</thead>
				<tbody class="divide-y {$glassTheme ? 'divide-white/[0.04]' : 'divide-surface-700/50'}">
					{#each entries as entry (entry.log_id)}
						<tr class="transition-colors hover:bg-surface-800/50 {entry.success ? '' : 'bg-red-900/10'}">
							<td class="whitespace-nowrap px-3 py-2 text-surface-300">{formatTime(entry.timestamp)}</td>
							<td class="px-3 py-2 font-medium text-surface-200">{entry.step}</td>
							<td class="px-3 py-2 text-surface-400">{entry.model_used ?? '-'}</td>
							<td class="whitespace-nowrap px-3 py-2 text-surface-400">
								{formatTokens(entry.input_tokens)} / {formatTokens(entry.output_tokens)}
							</td>
							<td class="px-3 py-2 text-surface-400">{entry.latency_ms}ms</td>
							<td class="px-3 py-2">
								{#if entry.success}
									<span class="rounded-full bg-green-900/30 px-2 py-0.5 text-green-400">OK</span>
								{:else}
									<span class="cursor-help rounded-full bg-red-900/30 px-2 py-0.5 text-red-400" role="note" onmouseenter={(e) => entry.error && showTooltip(e.currentTarget, entry.error, { maxWidth: 400, color: 'text-red-400' })} onmouseleave={hideTooltip}>ERR</span>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

{#if fixedTooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] max-w-sm whitespace-normal break-words rounded-md border border-transparent glass-tooltip px-2.5 py-1.5 text-laya-secondary font-normal {fixedTooltip.color ?? 'text-surface-200'}"
		style="top: {fixedTooltip.top}px; {fixedTooltip.right != null ? `right: ${fixedTooltip.right}px` : `left: ${fixedTooltip.left}px`}; max-width: {fixedTooltip.maxWidth}px;"
	>
		{fixedTooltip.text}
	</span>
{/if}
