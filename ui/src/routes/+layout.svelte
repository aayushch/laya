<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import HealthBadge from '$lib/components/HealthBadge.svelte';
	import ChatSidebar from '$lib/components/chat/ChatSidebar.svelte';
	import { initWebSocket, closeWebSocket } from '$lib/stores/websocket';
	import { startHealthPolling, stopHealthPolling } from '$lib/stores/health';
	import { chatOpen } from '$lib/stores/chat';
	import { theme } from '$lib/stores/theme';
	import { onMount } from 'svelte';

	let { children } = $props();
	let isSetupRoute = $derived(page.url.pathname.startsWith('/setup'));

	// Apply theme to <html> so CSS [data-theme] selectors work globally
	$effect(() => {
		document.documentElement.setAttribute('data-theme', $theme);
	});

	onMount(async () => {
		startHealthPolling();
		initWebSocket();

		if (!isSetupRoute) {
			try {
				const resp = await fetch('http://127.0.0.1:8420/settings/setup-status');
				if (resp.ok) {
					const data = await resp.json();
					if (!data.setup_complete) {
						goto('/setup');
					}
				}
			} catch {
				// Engine not ready yet — don't redirect
			}
		}

		return () => {
			stopHealthPolling();
			closeWebSocket();
		};
	});
</script>

{#if isSetupRoute}
	{@render children()}
{:else}
	<div class="flex h-screen flex-col bg-surface-900 text-surface-50">
		<!-- Header -->
		<header class="flex items-center justify-between border-b border-surface-700 bg-surface-900/95 px-6 py-3 backdrop-blur-sm">
			<div class="flex items-center gap-6">
				<!-- Branded logo -->
				<h1 class="text-xl font-bold tracking-wide text-laya-orange">Laya</h1>
				<nav class="flex items-center gap-1">
					<a
						href="/dashboard"
						class="rounded-md px-3 py-1.5 text-sm font-medium transition-colors
							{page.url.pathname === '/dashboard'
								? 'bg-laya-orange/10 text-laya-orange'
								: 'text-surface-400 hover:bg-surface-800 hover:text-surface-100'}"
					>Dashboard</a>
					<a
						href="/feed"
						class="rounded-md px-3 py-1.5 text-sm font-medium transition-colors
							{page.url.pathname === '/feed'
								? 'bg-laya-orange/10 text-laya-orange'
								: 'text-surface-400 hover:bg-surface-800 hover:text-surface-100'}"
					>Feed</a>
				</nav>
			</div>

			<div class="flex items-center gap-1">
				<!-- Chat -->
				<button
					onclick={() => chatOpen.update((v) => !v)}
					class="rounded-lg p-1.5 text-surface-400 transition-colors hover:bg-surface-800 hover:text-laya-orange"
					title="Chat with Laya"
				>
					<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
					</svg>
				</button>

				<!-- Settings gear -->
				<a
					href="/settings"
					class="rounded-lg p-1.5 transition-colors hover:bg-surface-800
						{page.url.pathname.startsWith('/settings')
							? 'text-laya-orange'
							: 'text-surface-400 hover:text-laya-orange'}"
					title="Settings"
				>
					<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
					</svg>
				</a>

				<!-- Health badge -->
				<a href="/" class="rounded-lg px-2 py-1.5 transition-colors hover:bg-surface-800" title="System status">
					<HealthBadge />
				</a>
			</div>
		</header>

		<!-- Main content -->
		<main class="flex-1 overflow-auto p-6">
			{@render children()}
		</main>
	</div>

	<ChatSidebar />
{/if}
