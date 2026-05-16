<script lang="ts">
	import { slide } from 'svelte/transition';
	import { engineApi } from '$lib/api/engine';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { glassTheme } from '$lib/stores/glassTheme';
	import PlatformIcon from './PlatformIcon.svelte';
	import type { EgressConnection } from '$lib/api/types';

	let {
		platformKey,
		label,
		category,
		isOAuth = false,
		connections = [],
		onConnect,
		onRefresh
	}: {
		platformKey: string;
		label: string;
		category: string;
		isOAuth?: boolean;
		connections: EgressConnection[];
		onConnect: (platform: string) => void;
		onRefresh: () => void;
	} = $props();

	let expanded = $state(false);
	let testingId = $state<string | null>(null);
	let testResult = $state<{ id: string; valid: boolean; error?: string } | null>(null);
	let disconnectingId = $state<string | null>(null);
	let confirmDisconnectId = $state<string | null>(null);
	let expandedErrors = $state<Set<string>>(new Set());

	const hasConnections = $derived(connections.length > 0);
	const anyConnected = $derived(connections.some(c => c.status === 'connected'));

	async function handleTest(conn: EgressConnection) {
		testingId = conn.connection_id;
		testResult = null;
		try {
			const result = await engineApi.testEgressConnection(conn.connection_id);
			testResult = { id: conn.connection_id, ...result };
			if (result.valid) {
				setTimeout(() => { if (testResult?.id === conn.connection_id) testResult = null; }, 3000);
			}
			onRefresh();
		} catch (e) {
			testResult = { id: conn.connection_id, valid: false, error: e instanceof Error ? e.message : 'Test failed' };
		} finally {
			testingId = null;
		}
	}

	async function confirmDisconnect(connId: string) {
		confirmDisconnectId = null;
		disconnectingId = connId;
		try {
			await engineApi.deleteEgressConnection(connId);
			onRefresh();
		} catch {
			// ignore
		} finally {
			disconnectingId = null;
		}
	}

	function toggleError(connId: string) {
		const next = new Set(expandedErrors);
		if (next.has(connId)) next.delete(connId);
		else next.add(connId);
		expandedErrors = next;
	}
</script>

<div class="relative">
	{#if !hasConnections}
		<!-- Unconnected: clickable row -->
		<button
			class="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors {$glassTheme ? 'hover:bg-white/[0.04]' : 'hover:bg-surface-700/30'}"
			onclick={() => onConnect(platformKey)}
		>
			<div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md {$glassTheme ? 'bg-white/[0.06] text-surface-400' : 'bg-surface-700/50 text-surface-400'}">
				<PlatformIcon platform={platformKey} size={18} />
			</div>
			<div class="min-w-0 flex-1">
				<span class="text-laya-base font-medium text-surface-200">{label}</span>
			</div>
			<span class="text-laya-secondary text-surface-500">Not connected</span>
		</button>
	{:else}
		<!-- Connected: row with expand toggle -->
		<button
			class="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors {$glassTheme ? 'hover:bg-white/[0.04]' : 'hover:bg-surface-700/30'}"
			onclick={() => expanded = !expanded}
		>
			<div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md
				{anyConnected ? 'bg-green-900/20 text-green-400' : 'bg-red-900/20 text-red-400'}">
				<PlatformIcon platform={platformKey} size={18} />
			</div>
			<div class="min-w-0 flex-1">
				<span class="text-laya-base font-medium text-surface-100">{label}</span>
			</div>
			<div class="flex items-center gap-2 shrink-0">
				{#if connections.length === 1}
					{@const conn = connections[0]}
					{#if conn.status === 'connected'}
						<span class="flex items-center gap-1.5 text-laya-secondary text-green-400/80">
							<span class="h-1.5 w-1.5 rounded-full bg-green-500"></span>
							<span class="max-w-[160px] truncate">{conn.name || 'Connected'}</span>
							{#if conn.capabilities?.length}
								<span class="text-surface-600">·</span>
								<span class="text-surface-500">{conn.capabilities.length} actions</span>
							{/if}
						</span>
					{:else}
						<span class="flex items-center gap-1.5 text-laya-secondary text-red-400/80">
							<span class="h-1.5 w-1.5 rounded-full bg-red-500"></span>
							<span class="max-w-[160px] truncate">{conn.error_message || 'Error'}</span>
						</span>
					{/if}
				{:else}
					<span class="flex items-center gap-1.5 text-laya-secondary text-surface-400">
						{#if anyConnected}
							<span class="h-1.5 w-1.5 rounded-full bg-green-500"></span>
						{/if}
						{connections.length} accounts
					</span>
				{/if}
				<svg
					class="h-4 w-4 text-surface-500 transition-transform {expanded ? 'rotate-90' : ''}"
					fill="none" stroke="currentColor" viewBox="0 0 24 24"
				>
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
				</svg>
			</div>
		</button>

		<!-- Expanded section -->
		{#if expanded}
			<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }} class="border-t px-4 py-3 space-y-2 {$glassTheme ? 'border-white/[0.06] bg-white/[0.02]' : 'border-surface-700/50 bg-surface-900/30'}">
				{#each connections as conn (conn.connection_id)}
					<div class="flex items-center justify-between gap-2 rounded px-3 py-2 {$glassTheme ? 'bg-white/[0.04]' : 'bg-surface-800/60'}">
						<div class="min-w-0 flex-1">
							<div class="flex items-center gap-1.5 text-laya-secondary">
								{#if conn.status === 'connected'}
									<span class="h-1.5 w-1.5 shrink-0 rounded-full bg-green-500"></span>
									<span class="text-surface-200 truncate">{conn.name || 'Connected'}</span>
									{#if conn.capabilities?.length}
										<span class="text-surface-600">·</span>
										<span class="text-surface-500">{conn.capabilities.length} actions</span>
									{/if}
								{:else}
									<span class="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500"></span>
									<!-- svelte-ignore a11y_click_events_have_key_events -->
									<!-- svelte-ignore a11y_no_static_element_interactions -->
									<span
										class="text-red-400/80 {expandedErrors.has(conn.connection_id) ? 'whitespace-pre-wrap select-text' : 'truncate'} cursor-pointer"
										onclick={(e) => { e.stopPropagation(); toggleError(conn.connection_id); }}
									>
										{conn.name || conn.error_message || 'Error'}
									</span>
								{/if}
							</div>
						</div>
						<div class="flex items-center gap-2 shrink-0">
							{#if conn.status !== 'connected'}
								<button
									onclick={(e) => { e.stopPropagation(); onConnect(platformKey); }}
									class="text-laya-secondary text-laya-orange hover:text-laya-gold transition-colors"
								>
									Reconnect
								</button>
							{/if}
							<button
								onclick={(e) => { e.stopPropagation(); handleTest(conn); }}
								disabled={testingId === conn.connection_id}
								class="text-laya-secondary text-surface-500 hover:text-surface-300 transition-colors disabled:opacity-50"
							>
								{testingId === conn.connection_id ? '...' : 'Test'}
							</button>
							<button
								onclick={(e) => { e.stopPropagation(); confirmDisconnectId = conn.connection_id; }}
								disabled={disconnectingId === conn.connection_id}
								class="text-laya-secondary text-red-400/50 hover:text-red-400 transition-colors disabled:opacity-50"
							>
								{disconnectingId === conn.connection_id ? '...' : 'Remove'}
							</button>
						</div>
					</div>
					{#if testResult?.id === conn.connection_id}
						<div class="mx-3 rounded px-2 py-1 text-laya-secondary
							{testResult.valid ? 'bg-green-900/20 text-green-400' : 'bg-red-900/20 text-red-400'}">
							{testResult.valid ? 'Connection valid' : testResult.error || 'Test failed'}
						</div>
					{/if}
				{/each}

				<!-- Add another account -->
				<button
					onclick={(e) => { e.stopPropagation(); onConnect(platformKey); }}
					class="w-full rounded border border-dashed px-3 py-1.5 text-laya-secondary text-surface-500 hover:text-surface-300 transition-colors {$glassTheme ? 'border-white/[0.08] hover:border-white/[0.15]' : 'border-surface-700 hover:border-surface-500'}"
				>
					+ Add another account
				</button>
			</div>
		{/if}
	{/if}

	<!-- Disconnect confirmation overlay -->
	{#if confirmDisconnectId}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="absolute inset-0 z-10 flex items-center justify-center backdrop-blur-sm {$glassTheme ? 'bg-black/60' : 'bg-surface-900/95'}"
			onkeydown={(e) => { if (e.key === 'Escape') confirmDisconnectId = null; }}
		>
			<div class="px-4 py-3 text-center">
				<p class="text-laya-secondary font-medium text-surface-200 mb-1">Disconnect this account?</p>
				<p class="text-laya-secondary text-surface-400 mb-3">This will remove credentials and deactivate associated workflows.</p>
				<div class="flex items-center justify-center gap-2">
					<button
						onclick={() => confirmDisconnectId = null}
						class="rounded px-3 py-1 text-laya-secondary text-surface-300 bg-surface-700 hover:bg-surface-600 transition-colors"
					>
						Cancel
					</button>
					<button
						onclick={() => { if (confirmDisconnectId) confirmDisconnect(confirmDisconnectId); }}
						class="rounded px-3 py-1 text-laya-secondary text-white bg-red-600 hover:bg-red-500 transition-colors"
					>
						Disconnect
					</button>
				</div>
			</div>
		</div>
	{/if}
</div>
