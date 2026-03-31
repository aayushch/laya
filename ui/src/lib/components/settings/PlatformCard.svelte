<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import PlatformIcon from './PlatformIcon.svelte';
	import type { EgressConnection } from '$lib/api/types';

	let {
		platformKey,
		label,
		category,
		isOAuth = false,
		connection = null,
		onConnect,
		onRefresh
	}: {
		platformKey: string;
		label: string;
		category: string;
		isOAuth?: boolean;
		connection: EgressConnection | null;
		onConnect: (platform: string) => void;
		onRefresh: () => void;
	} = $props();

	let testing = $state(false);
	let testResult = $state<{ valid: boolean; error?: string } | null>(null);
	let disconnecting = $state(false);

	const isConnected = $derived(connection?.status === 'connected');
	const isError = $derived(connection?.status === 'error' || connection?.status === 'expired');
	const capCount = $derived(connection?.capabilities?.length ?? 0);

	async function handleTest() {
		if (!connection) return;
		testing = true;
		testResult = null;
		try {
			testResult = await engineApi.testEgressConnection(connection.connection_id);
			if (testResult.valid) {
				setTimeout(() => (testResult = null), 3000);
			}
			onRefresh();
		} catch (e) {
			testResult = { valid: false, error: e instanceof Error ? e.message : 'Test failed' };
		} finally {
			testing = false;
		}
	}

	async function handleDisconnect() {
		if (!connection || !confirm(`Disconnect ${label}? This will remove the stored credentials.`)) return;
		disconnecting = true;
		try {
			await engineApi.deleteEgressConnection(connection.connection_id);
			onRefresh();
		} catch {
			// ignore
		} finally {
			disconnecting = false;
		}
	}
</script>

<div
	class="group relative rounded-lg border p-4 transition-colors
		{isConnected
			? 'border-l-2 border-l-green-500 border-t-surface-700 border-r-surface-700 border-b-surface-700 bg-surface-800'
			: isError
				? 'border-l-2 border-l-red-500 border-t-surface-700 border-r-surface-700 border-b-surface-700 bg-surface-800'
				: 'border-surface-700 bg-surface-800/60 hover:bg-surface-800 hover:border-surface-600 cursor-pointer'}"
	role={!connection ? 'button' : undefined}
	tabindex={!connection ? 0 : undefined}
	onclick={() => { if (!connection) onConnect(platformKey); }}
	onkeydown={(e) => { if (!connection && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onConnect(platformKey); } }}
>
	<div class="flex items-center gap-3">
		<div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg
			{isConnected ? 'bg-green-900/20 text-green-400' : isError ? 'bg-red-900/20 text-red-400' : 'bg-surface-700 text-surface-400'}">
			<PlatformIcon platform={platformKey} size={20} />
		</div>
		<div class="min-w-0 flex-1">
			<div class="flex items-center gap-2">
				<span class="text-sm font-medium text-surface-100">{label}</span>
				{#if isOAuth}
					<span class="rounded bg-surface-700 px-1 py-0.5 text-[9px] uppercase tracking-wider text-surface-500">OAuth</span>
				{/if}
			</div>
			{#if isConnected}
				<div class="flex items-center gap-1.5 text-xs text-green-400/80">
					<span class="h-1.5 w-1.5 rounded-full bg-green-500"></span>
					Connected
					{#if capCount > 0}
						<span class="text-surface-500">·</span>
						<span class="text-surface-400">{capCount} actions</span>
					{/if}
				</div>
			{:else if isError}
				<div class="text-xs text-red-400/80 truncate" title={connection?.error_message ?? ''}>
					{connection?.error_message || 'Connection error'}
				</div>
			{:else}
				<div class="text-xs text-surface-500">Not connected</div>
			{/if}
		</div>
	</div>

	<!-- Test result toast -->
	{#if testResult}
		<div class="mt-2 rounded px-2 py-1 text-xs
			{testResult.valid ? 'bg-green-900/20 text-green-400' : 'bg-red-900/20 text-red-400'}">
			{testResult.valid ? 'Connection valid' : testResult.error || 'Test failed'}
		</div>
	{/if}

	<!-- Actions for connected/error platforms -->
	{#if connection}
		<div class="mt-3 flex items-center gap-2 border-t border-surface-700/50 pt-2">
			{#if isError}
				<button
					onclick={() => onConnect(platformKey)}
					class="text-xs text-laya-orange hover:text-laya-gold transition-colors"
				>
					Reconnect
				</button>
			{/if}
			<button
				onclick={handleTest}
				disabled={testing}
				class="text-xs text-surface-400 hover:text-surface-200 transition-colors disabled:opacity-50"
			>
				{testing ? 'Testing...' : 'Test'}
			</button>
			<button
				onclick={handleDisconnect}
				disabled={disconnecting}
				class="ml-auto text-xs text-red-400/60 hover:text-red-400 transition-colors disabled:opacity-50"
			>
				{disconnecting ? '...' : 'Disconnect'}
			</button>
		</div>
	{/if}
</div>
