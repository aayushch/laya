<script lang="ts">
	import { onMount } from 'svelte';
	import { health, healthError } from '$lib/stores/health';
	import { wsStatus, lastMessage } from '$lib/stores/websocket';

	function statusIcon(status: string | undefined): string {
		if (status === 'healthy' || status === 'connected' || status === 'available') return 'text-green-400';
		if (status === 'unreachable' || status === 'not_configured') return 'text-surface-500';
		return 'text-red-400';
	}

	function statusLabel(status: string | undefined, fallback = 'unknown'): string {
		return status ?? fallback;
	}

	// Docker/n8n management (only available in Tauri)
	let dockerAvailable = $state(false);
	let n8nContainerStatus = $state('checking...');
	let dockerAction = $state('');

	async function invoke(cmd: string): Promise<any> {
		try {
			const { invoke: tauriInvoke } = await import('@tauri-apps/api/core');
			return await tauriInvoke(cmd);
		} catch {
			return null;
		}
	}

	async function checkDocker() {
		const available = await invoke('check_docker');
		dockerAvailable = available === true;
		if (dockerAvailable) {
			const status = await invoke('n8n_status');
			n8nContainerStatus = status ?? 'unknown';
		} else {
			n8nContainerStatus = 'docker not available';
		}
	}

	async function startN8n() {
		dockerAction = 'starting';
		await invoke('start_n8n');
		await new Promise((r) => setTimeout(r, 2000));
		await checkDocker();
		dockerAction = '';
	}

	async function stopN8n() {
		dockerAction = 'stopping';
		await invoke('stop_n8n');
		await new Promise((r) => setTimeout(r, 1000));
		await checkDocker();
		dockerAction = '';
	}

	onMount(() => {
		checkDocker();
	});
</script>

<div class="mx-auto max-w-2xl space-y-8">
	<div>
		<h2 class="mb-1 text-2xl font-semibold">System Status</h2>
		<p class="text-sm text-surface-400">Milestone 1 — Skeleton</p>
	</div>

	<!-- Service health cards -->
	<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
		<!-- Engine -->
		<div class="rounded-xl border border-surface-700 bg-surface-800 p-5">
			<div class="mb-2 text-xs uppercase tracking-wider text-surface-400">Engine</div>
			{#if $healthError || !$health}
				<span class="text-red-400">Offline</span>
			{:else}
				<span class={statusIcon($health.engine)}>{statusLabel($health.engine)}</span>
				<div class="mt-2 text-xs text-surface-500">Uptime: {$health.uptime_seconds}s</div>
			{/if}
		</div>

		<!-- SQLite -->
		<div class="rounded-xl border border-surface-700 bg-surface-800 p-5">
			<div class="mb-2 text-xs uppercase tracking-wider text-surface-400">SQLite</div>
			{#if $healthError || !$health}
				<span class="text-red-400">Offline</span>
			{:else}
				<span class={statusIcon($health.sqlite)}>{statusLabel($health.sqlite)}</span>
			{/if}
		</div>

		<!-- n8n -->
		<div class="rounded-xl border border-surface-700 bg-surface-800 p-5">
			<div class="mb-2 text-xs uppercase tracking-wider text-surface-400">n8n</div>
			{#if $healthError || !$health}
				<span class="text-red-400">Offline</span>
			{:else}
				<span class={statusIcon($health.n8n)}>{statusLabel($health.n8n)}</span>
			{/if}
		</div>

		<!-- WebSocket -->
		<div class="rounded-xl border border-surface-700 bg-surface-800 p-5">
			<div class="mb-2 text-xs uppercase tracking-wider text-surface-400">WebSocket</div>
			<span class={statusIcon($wsStatus === 'connected' ? 'healthy' : 'unhealthy')}>
				{$wsStatus}
			</span>
		</div>
	</div>

	<!-- Docker / n8n control -->
	{#if dockerAvailable}
		<div class="rounded-xl border border-surface-700 bg-surface-800 p-5">
			<div class="mb-3 flex items-center justify-between">
				<div>
					<div class="text-xs uppercase tracking-wider text-surface-400">n8n Container</div>
					<div class="mt-1 text-sm">
						<span
							class={n8nContainerStatus === 'running'
								? 'text-green-400'
								: 'text-surface-400'}
						>
							{n8nContainerStatus}
						</span>
					</div>
				</div>
				<div class="flex gap-2">
					{#if n8nContainerStatus !== 'running'}
						<button
							class="rounded-md bg-green-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-green-500 disabled:opacity-50"
							onclick={startN8n}
							disabled={!!dockerAction}
						>
							{dockerAction === 'starting' ? 'Starting...' : 'Start'}
						</button>
					{:else}
						<button
							class="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-500 disabled:opacity-50"
							onclick={stopN8n}
							disabled={!!dockerAction}
						>
							{dockerAction === 'stopping' ? 'Stopping...' : 'Stop'}
						</button>
					{/if}
				</div>
			</div>
		</div>
	{/if}

	<!-- Last message -->
	{#if $lastMessage}
		<div class="rounded-xl border border-surface-700 bg-surface-800 p-5">
			<div class="mb-2 text-xs uppercase tracking-wider text-surface-400">Last WS Message</div>
			<pre class="overflow-x-auto text-xs text-surface-300">{JSON.stringify($lastMessage, null, 2)}</pre>
		</div>
	{/if}
</div>
