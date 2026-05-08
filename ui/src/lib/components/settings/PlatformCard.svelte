<script lang="ts">
	import { engineApi } from '$lib/api/engine';
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

<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
	class="group relative rounded-lg border p-4 transition-colors
		{anyConnected
			? 'border-l-2 border-l-green-500 border-t-surface-700 border-r-surface-700 border-b-surface-700 bg-surface-800'
			: hasConnections
				? 'border-l-2 border-l-red-500 border-t-surface-700 border-r-surface-700 border-b-surface-700 bg-surface-800'
				: 'border-surface-700 bg-surface-800/60 hover:bg-surface-800 hover:border-surface-600 cursor-pointer'}"
	role={!hasConnections ? 'button' : undefined}
	tabindex={!hasConnections ? 0 : undefined}
	onclick={() => { if (!hasConnections) onConnect(platformKey); }}
	onkeydown={(e) => { if (!hasConnections && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onConnect(platformKey); } }}
>
	<!-- Platform header -->
	<div class="flex items-center gap-3">
		<div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg
			{anyConnected ? 'bg-green-900/20 text-green-400' : hasConnections ? 'bg-red-900/20 text-red-400' : 'bg-surface-700 text-surface-400'}">
			<PlatformIcon platform={platformKey} size={20} />
		</div>
		<div class="min-w-0 flex-1">
			<div class="flex items-center gap-2">
				<span class="text-laya-base font-medium text-surface-100">{label}</span>
				{#if isOAuth}
					<span class="rounded bg-surface-700 px-1 py-0.5 text-laya-micro uppercase tracking-wider text-surface-500">OAuth</span>
				{/if}
			</div>
			{#if !hasConnections}
				<div class="text-laya-secondary text-surface-500">Not connected</div>
			{:else if connections.length === 1}
				{@const conn = connections[0]}
				{#if conn.status === 'connected'}
					<div class="flex items-center gap-1.5 text-laya-secondary text-green-400/80">
						<span class="h-1.5 w-1.5 rounded-full bg-green-500"></span>
						{conn.name || 'Connected'}
						{#if conn.capabilities?.length}
							<span class="text-surface-500">·</span>
							<span class="text-surface-400">{conn.capabilities.length} actions</span>
						{/if}
					</div>
				{:else}
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div
						class="text-laya-secondary text-red-400/80 {expandedErrors.has(conn.connection_id) ? 'whitespace-pre-wrap select-text' : 'truncate'} cursor-pointer"
						onclick={(e) => { e.stopPropagation(); toggleError(conn.connection_id); }}
					>
						{conn.error_message || 'Connection error'}
					</div>
				{/if}
			{:else}
				<div class="text-laya-secondary text-surface-400">{connections.length} accounts</div>
			{/if}
		</div>
	</div>

	<!-- Multiple connections list -->
	{#if connections.length > 1}
		<div class="mt-3 space-y-2 border-t border-surface-700/50 pt-2">
			{#each connections as conn (conn.connection_id)}
				<div class="flex items-center justify-between gap-2 rounded px-2 py-1.5 bg-surface-900/50">
					<div class="min-w-0 flex-1">
						<div class="flex items-center gap-1.5 text-laya-secondary">
							{#if conn.status === 'connected'}
								<span class="h-1.5 w-1.5 shrink-0 rounded-full bg-green-500"></span>
								<span class="text-surface-200 truncate">{conn.name || 'Connected'}</span>
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
					<div class="flex items-center gap-1.5 shrink-0">
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
					<div class="mx-2 rounded px-2 py-1 text-laya-secondary
						{testResult.valid ? 'bg-green-900/20 text-green-400' : 'bg-red-900/20 text-red-400'}">
						{testResult.valid ? 'Connection valid' : testResult.error || 'Test failed'}
					</div>
				{/if}
			{/each}
		</div>
	{/if}

	<!-- Single connection actions -->
	{#if connections.length === 1}
		{@const conn = connections[0]}
		{#if testResult?.id === conn.connection_id}
			<div class="mt-2 rounded px-2 py-1 text-laya-secondary
				{testResult.valid ? 'bg-green-900/20 text-green-400' : 'bg-red-900/20 text-red-400'}">
				{testResult.valid ? 'Connection valid' : testResult.error || 'Test failed'}
			</div>
		{/if}
		<div class="mt-3 flex items-center gap-2 border-t border-surface-700/50 pt-2">
			{#if conn.status !== 'connected'}
				<button
					onclick={() => onConnect(platformKey)}
					class="text-laya-secondary text-laya-orange hover:text-laya-gold transition-colors"
				>
					Reconnect
				</button>
			{/if}
			<button
				onclick={() => handleTest(conn)}
				disabled={testingId === conn.connection_id}
				class="text-laya-secondary text-surface-400 hover:text-surface-200 transition-colors disabled:opacity-50"
			>
				{testingId === conn.connection_id ? 'Testing...' : 'Test'}
			</button>
			<button
				onclick={() => confirmDisconnectId = conn.connection_id}
				disabled={disconnectingId === conn.connection_id}
				class="ml-auto text-laya-secondary text-red-400/60 hover:text-red-400 transition-colors disabled:opacity-50"
			>
				{disconnectingId === conn.connection_id ? '...' : 'Disconnect'}
			</button>
		</div>
	{/if}

	<!-- Add another account button -->
	{#if hasConnections}
		<button
			onclick={(e) => { e.stopPropagation(); onConnect(platformKey); }}
			class="mt-2 w-full rounded border border-dashed border-surface-700 px-2 py-1 text-laya-secondary text-surface-500 hover:text-surface-300 hover:border-surface-500 transition-colors"
		>
			+ Add another account
		</button>
	{/if}

	<!-- Disconnect confirmation popover -->
	{#if confirmDisconnectId}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-surface-900/95 backdrop-blur-sm"
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
