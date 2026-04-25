<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import type { AuditLogEntry, DeadEvent } from '$lib/api/types';

	// ── Audit log state ──
	let entries: AuditLogEntry[] = $state([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state('');

	let filterStep = $state('');
	let filterSuccess = $state<'' | 'true' | 'false'>('');
	let limit = 25;
	let offset = $state(0);

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

	// ── Audit log functions ──
	async function load() {
		loading = true;
		error = '';
		try {
			const params: Record<string, unknown> = { limit, offset };
			if (filterStep) params.step = filterStep;
			if (filterSuccess !== '') params.success = filterSuccess === 'true';
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
			second: '2-digit'
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
</script>

<div class="space-y-4">
	<!-- Dead Events Recovery -->
	{#if !deadLoading && deadTotal > 0}
		<div class="rounded-lg border border-amber-600/50 bg-amber-900/20 p-3">
			<div class="flex items-center justify-between">
				<div class="flex items-center gap-2">
					<svg class="h-4 w-4 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
					</svg>
					<span class="text-sm font-medium text-amber-300">
						{deadTotal} event{deadTotal !== 1 ? 's' : ''} failed permanently
					</span>
				</div>
				<div class="flex items-center gap-2">
					<button
						onclick={() => { deadExpanded = !deadExpanded; retryAllConfirm = false; }}
						class="rounded px-3 py-1 text-xs font-medium text-surface-300 transition-colors hover:bg-surface-700"
					>
						{deadExpanded ? 'Hide' : 'View'}
					</button>
					<button
						onclick={retryAll}
						disabled={retryingAll}
						class="rounded bg-laya-orange/20 px-3 py-1 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:opacity-50"
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
				<div class="mt-3 overflow-visible {$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700'}">
					<table class="w-full table-fixed text-left text-xs">
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
									<td class="whitespace-nowrap px-3 py-2 text-surface-300">
										{formatTime(evt.created_at)}
									</td>
									<td class="px-3 py-2">
										<span class="rounded bg-surface-700 px-1.5 py-0.5 text-surface-300">
											{evt.source_platform}
										</span>
									</td>
									<td class="group/subj relative overflow-visible px-3 py-2 font-medium text-surface-200">
										<span class="block truncate">
											{#if evt.subject_url}
												<a href={evt.subject_url} target="_blank" rel="noopener" class="hover:text-laya-orange hover:underline">
													{evt.subject_title}
												</a>
											{:else}
												{evt.subject_title}
											{/if}
										</span>
										{#if evt.subject_title && evt.subject_title.length > 25}
											<div class="pointer-events-none absolute bottom-full left-0 z-50 mb-1.5 hidden max-w-sm whitespace-normal break-words rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-xs font-normal text-surface-200 shadow-lg group-hover/subj:block">
												{evt.subject_title}
											</div>
										{/if}
									</td>
									<td class="group/err relative overflow-visible px-3 py-2 text-red-400">
										<span class="block truncate">
											{truncateError(evt.last_error)}
										</span>
										{#if evt.last_error && evt.last_error.length > 30}
											<div class="pointer-events-none absolute bottom-full left-0 z-50 mb-1.5 hidden max-w-sm whitespace-normal break-words rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-xs font-normal text-red-300 shadow-lg group-hover/err:block">
												{evt.last_error}
											</div>
										{/if}
									</td>
									<td class="whitespace-nowrap px-3 py-2 text-surface-400">
										{evt.processing_attempts} attempt{evt.processing_attempts !== 1 ? 's' : ''}{#if evt.manual_retries > 0}, retried {evt.manual_retries}x{/if}
									</td>
									<td class="px-3 py-2">
										<button
											onclick={() => retryOne(evt.event_id)}
											disabled={retrying.has(evt.event_id)}
											class="rounded bg-laya-orange/20 px-2 py-0.5 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:opacity-50"
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

	<!-- Filters -->
	<div class="flex flex-wrap items-end gap-3">
		<div>
			<label for="step-filter" class="mb-1 block text-xs text-surface-400">Step</label>
			<input
				id="step-filter"
				bind:value={filterStep}
				placeholder="e.g. router"
				class="rounded-lg border border-surface-600 bg-surface-800 px-3 py-1.5 text-sm text-surface-200 placeholder-surface-500 focus:border-blue-500 focus:outline-none"
			/>
		</div>
		<div>
			<label for="success-filter" class="mb-1 block text-xs text-surface-400">Status</label>
			<select
				id="success-filter"
				bind:value={filterSuccess}
				class="rounded-lg border border-surface-600 bg-surface-800 px-3 py-1.5 text-sm text-surface-200 focus:border-blue-500 focus:outline-none"
			>
				<option value="">All</option>
				<option value="true">Success</option>
				<option value="false">Failed</option>
			</select>
		</div>
		<button
			onclick={applyFilter}
			class="rounded-lg bg-surface-700 px-4 py-1.5 text-sm font-medium text-surface-200 transition-colors hover:bg-surface-600"
		>
			Apply
		</button>
	</div>

	{#if loading}
		<div class="flex justify-center py-8">
			<div class="h-5 w-5 animate-spin rounded-full border-2 border-blue-400 border-t-transparent"></div>
		</div>
	{:else if error}
		<div class="rounded-lg border border-red-800 bg-red-900/20 p-3 text-sm text-red-300">{error}</div>
	{:else if entries.length === 0}
		<p class="py-8 text-center text-sm text-surface-500">No audit log entries found.</p>
	{:else}
		<div class="overflow-x-auto {$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700'}">
			<table class="w-full text-left text-xs">
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
									<span class="rounded-full bg-red-900/30 px-2 py-0.5 text-red-400" title={entry.error ?? ''}>ERR</span>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		<!-- Pagination -->
		<div class="flex items-center justify-between text-xs text-surface-400">
			<span>{total} entries</span>
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
	{/if}
</div>
