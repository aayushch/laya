<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { health, healthError } from '$lib/stores/health';
	import { wsStatus } from '$lib/stores/websocket';
	import { vectorStoreState } from '$lib/utils/vectorStore';
	import { modelProblems } from '$lib/utils/modelAvailability';

	// Yellow = the engine is usable but degraded: live updates are disconnected,
	// the vector store (semantic search) is still starting or unavailable, or a
	// provider refused a configured model/key (#25).
	let statusColor = $derived.by(() => {
		if ($healthError || !$health) return 'bg-red-500';
		if ($health.engine === 'healthy' && $health.sqlite === 'healthy') {
			const degraded =
				$wsStatus !== 'connected' ||
				vectorStoreState($health) !== 'ready' ||
				modelProblems($health).length > 0;
			return degraded ? 'bg-yellow-500' : 'bg-green-500';
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

<span class="relative flex h-2.5 w-2.5">
	{#if statusColor === 'bg-green-500'}
		<span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75"></span>
	{/if}
	<span class="relative inline-flex h-2.5 w-2.5 rounded-full {statusColor}"></span>
</span>
