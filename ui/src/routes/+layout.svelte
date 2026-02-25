<script lang="ts">
	import '../app.css';
	import HealthBadge from '$lib/components/HealthBadge.svelte';
	import ChatSidebar from '$lib/components/chat/ChatSidebar.svelte';
	import { initWebSocket, closeWebSocket } from '$lib/stores/websocket';
	import { startHealthPolling, stopHealthPolling } from '$lib/stores/health';
	import { chatOpen } from '$lib/stores/chat';
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
				<a href="/feed" class="text-sm text-surface-400 transition-colors hover:text-surface-100">Feed</a>
				<a href="/dashboard" class="text-sm text-surface-400 transition-colors hover:text-surface-100">Dashboard</a>
				<a href="/settings" class="text-sm text-surface-400 transition-colors hover:text-surface-100">Settings</a>
			</nav>
		</div>
		<div class="flex items-center gap-3">
			<button
				onclick={() => chatOpen.update((v) => !v)}
				class="rounded-lg p-1.5 text-surface-400 transition-colors hover:bg-surface-700 hover:text-surface-200"
				title="Chat with Laya"
			>
				<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
				</svg>
			</button>
			<HealthBadge />
		</div>
	</header>

	<!-- Main content -->
	<main class="flex-1 overflow-auto p-6">
		{@render children()}
	</main>
</div>

<ChatSidebar />
