<script lang="ts">
	import '../app.css';
	import HealthBadge from '$lib/components/HealthBadge.svelte';
	import { initWebSocket, closeWebSocket } from '$lib/stores/websocket';
	import { startHealthPolling, stopHealthPolling } from '$lib/stores/health';
	import { onMount } from 'svelte';

	let { children } = $props();

	onMount(() => {
		startHealthPolling();
		initWebSocket();
		return () => {
			stopHealthPolling();
			closeWebSocket();
		};
	});
</script>

<div class="flex h-screen flex-col bg-surface-900 text-surface-50">
	<!-- Header -->
	<header class="flex items-center justify-between border-b border-surface-700 px-6 py-3">
		<div class="flex items-center gap-6">
			<h1 class="text-xl font-bold tracking-wide">Laya</h1>
			<nav class="flex items-center gap-4">
				<a href="/" class="text-sm text-surface-400 transition-colors hover:text-surface-100">Status</a>
				<a href="/settings" class="text-sm text-surface-400 transition-colors hover:text-surface-100">Settings</a>
			</nav>
		</div>
		<HealthBadge />
	</header>

	<!-- Main content -->
	<main class="flex-1 overflow-auto p-6">
		{@render children()}
	</main>
</div>
