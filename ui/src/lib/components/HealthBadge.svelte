<script lang="ts">
	import { health, healthError } from '$lib/stores/health';
	import { wsStatus } from '$lib/stores/websocket';

	let statusColor = $derived.by(() => {
		if ($healthError || !$health) return 'bg-red-500';
		if ($health.engine === 'healthy' && $health.sqlite === 'healthy') {
			return $wsStatus === 'connected' ? 'bg-green-500' : 'bg-yellow-500';
		}
		return 'bg-red-500';
	});

	let statusText = $derived.by(() => {
		if ($healthError || !$health) return 'Offline';
		if ($health.engine === 'healthy' && $wsStatus === 'connected') return 'Connected';
		if ($health.engine === 'healthy') return 'Engine OK';
		return 'Unhealthy';
	});
</script>

<div class="flex items-center gap-2">
	<span class="relative flex h-2.5 w-2.5">
		{#if statusColor === 'bg-green-500'}
			<span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75"></span>
		{/if}
		<span class="relative inline-flex h-2.5 w-2.5 rounded-full {statusColor}"></span>
	</span>
	<span class="text-sm text-surface-300">{statusText}</span>
</div>
