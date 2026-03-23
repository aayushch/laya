<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import type { AuditLogEntry } from '$lib/api/types';

	let entries: AuditLogEntry[] = $state([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state('');

	let filterStep = $state('');
	let filterSuccess = $state<'' | 'true' | 'false'>('');
	let limit = 25;
	let offset = $state(0);

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
</script>

<div class="space-y-4">
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
		<div class="overflow-x-auto rounded-lg border border-surface-700">
			<table class="w-full text-left text-xs">
				<thead class="border-b border-surface-700 bg-surface-800 text-surface-400">
					<tr>
						<th class="px-3 py-2">Time</th>
						<th class="px-3 py-2">Step</th>
						<th class="px-3 py-2">Model</th>
						<th class="px-3 py-2">Tokens</th>
						<th class="px-3 py-2">Latency</th>
						<th class="px-3 py-2">Status</th>
					</tr>
				</thead>
				<tbody class="divide-y divide-surface-700/50">
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
