<script lang="ts">
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

	<!-- Last message -->
	{#if $lastMessage}
		<div class="rounded-xl border border-surface-700 bg-surface-800 p-5">
			<div class="mb-2 text-xs uppercase tracking-wider text-surface-400">Last WS Message</div>
			<pre class="overflow-x-auto text-xs text-surface-300">{JSON.stringify($lastMessage, null, 2)}</pre>
		</div>
	{/if}
</div>
