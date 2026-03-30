<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import HealthBadge from '$lib/components/HealthBadge.svelte';
	import ChatSidebar from '$lib/components/chat/ChatSidebar.svelte';
	import { initWebSocket, closeWebSocket, lastMessage } from '$lib/stores/websocket';
	import { startHealthPolling, stopHealthPolling, startupReady } from '$lib/stores/health';
	import StartupScreen from '$lib/components/StartupScreen.svelte';
	import { needsSetup, setupComplete } from '$lib/stores/setup';
	import { chatOpen, chatListOpen } from '$lib/stores/chat';
	import { theme } from '$lib/stores/theme';
	import { budgetPaused, loadBudgetStatus, handleBudgetWsMessage } from '$lib/stores/budget';
	import { feedFilters, loadFeedFilters, saveFeedFilters, filtersLoaded, feedDate, feedPrevDate, feedNextDate, localToday } from '$lib/stores/feedFilters';
	import { spaces, loadSpaces } from '$lib/stores/spaces';
	import { onMount } from 'svelte';

	function formatDateLabel(dateStr: string): string {
		const today = localToday();
		const d = new Date();
		d.setDate(d.getDate() - 1);
		const yesterday = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
		if (dateStr === today) return 'Today';
		if (dateStr === yesterday) return 'Yesterday';
		return new Date(dateStr + 'T00:00:00').toLocaleDateString(undefined, {
			weekday: 'short',
			month: 'short',
			day: 'numeric'
		});
	}

	const isToday = $derived($feedDate === localToday());

	let { children } = $props();
	let isSetupRoute = $derived(page.url.pathname.startsWith('/setup'));
	let isFeedRoute = $derived(page.url.pathname === '/feed' || page.url.pathname === '/');

	let headerEl = $state<HTMLElement | null>(null);

	// Measure header and expose as CSS variable for chat sidebar positioning
	$effect(() => {
		if (!headerEl) return;
		const ro = new ResizeObserver(([e]) => {
			document.documentElement.style.setProperty('--header-h', `${e.borderBoxSize[0].blockSize}px`);
		});
		ro.observe(headerEl);
		return () => ro.disconnect();
	});

	// Apply theme to <html> so CSS [data-theme] selectors work globally
	$effect(() => {
		document.documentElement.setAttribute('data-theme', $theme);
	});

	// Persist when filters change (only after initial load to avoid overwriting saved prefs with defaults)
	$effect(() => {
		$feedFilters;
		if (filtersLoaded()) saveFeedFilters();
	});

	// Check setup status once engine is ready
	let setupChecked = $state(false);
	$effect(() => {
		if (!$startupReady || setupChecked || isSetupRoute) return;
		setupChecked = true;
		fetch('http://127.0.0.1:8420/settings/setup-status')
			.then((resp) => resp.ok ? resp.json() : null)
			.then((data) => {
				if (data && !data.setup_complete) goto('/setup');
			})
			.catch(() => {});
		// Load spaces, feed filters, and budget status once engine is available
		loadSpaces();
		loadFeedFilters();
		loadBudgetStatus();
	});

	// React to budget WebSocket messages
	$effect(() => {
		const msg = $lastMessage;
		if (msg && msg.type === 'budget_status') {
			handleBudgetWsMessage(msg as any);
		}
	});

	onMount(() => {
		startHealthPolling();
		initWebSocket();

		// Auto-advance feedDate at midnight so "Today"/"Yesterday" labels stay correct
		function scheduleMidnightUpdate() {
			const now = Date.now();
			const midnight = new Date(new Date().toDateString()).getTime() + 86_400_000;
			return setTimeout(() => {
				feedDate.set(localToday());
				midnightTimer = scheduleMidnightUpdate();
			}, midnight - now);
		}
		let midnightTimer = scheduleMidnightUpdate();

		return () => {
			clearTimeout(midnightTimer);
			stopHealthPolling();
			closeWebSocket();
		};
	});

</script>

{#if $needsSetup || !$startupReady}
	<StartupScreen />
{:else if isSetupRoute}
	{@render children()}
{:else}
	<div class="flex h-screen flex-col bg-surface-900 text-surface-50">
		<!-- Budget paused banner -->
		{#if $budgetPaused}
			<div class="flex items-center justify-center gap-2 bg-red-500/15 border-b border-red-500/30 px-4 py-1.5">
				<svg class="h-3.5 w-3.5 text-red-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z" />
				</svg>
				<span class="text-xs text-red-300">Monthly budget limit reached — all ingestion workflows are paused</span>
				<a href="/settings?tab=models&section=cost-control" class="ml-1 text-xs font-medium text-red-400 underline underline-offset-2 hover:text-red-300">Manage</a>
			</div>
		{/if}

		<!-- Header -->
		<header bind:this={headerEl} class="relative z-50 flex items-center border-b border-surface-700 bg-surface-900/95 px-5 py-2.5 backdrop-blur-sm">
			<!-- Left: Logo + Primary Nav -->
			<div class="flex items-center gap-1 mr-4">
				<a href="/feed" class="flex items-center gap-2 mr-3">
					<h1 class="text-xl font-bold tracking-wide text-laya-orange">Laya</h1>
				</a>
				<nav class="flex items-center gap-0.5">
					<a
						href="/feed"
						class="rounded-lg px-3 py-1.5 text-sm font-medium transition-colors
							{isFeedRoute
								? 'bg-laya-orange/10 text-laya-orange'
								: 'text-surface-400 hover:text-surface-200 hover:bg-surface-800'}"
					>Feed</a>
					<a
						href="/coherence"
						class="rounded-lg px-3 py-1.5 text-sm font-medium transition-colors
							{page.url.pathname.startsWith('/coherence')
								? 'bg-laya-orange/10 text-laya-orange'
								: 'text-surface-400 hover:text-surface-200 hover:bg-surface-800'}"
					>Coherence</a>
				</nav>
			</div>

			<!-- Center: Date navigation (absolutely centered, only on feed route) -->
			{#if isFeedRoute}
				<div class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
					{#if $feedFilters.showBookmarked}
						<div class="flex items-center gap-1.5">
							<svg class="h-3.5 w-3.5 text-laya-orange" fill="currentColor" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
							</svg>
							<span class="text-xs font-medium text-laya-orange whitespace-nowrap">Bookmarked</span>
						</div>
					{:else}
						<div class="flex items-center gap-1">
							<button
								class="rounded-md p-1.5 text-surface-400 transition-colors hover:bg-surface-800 hover:text-surface-200 disabled:opacity-30 disabled:hover:bg-transparent"
								disabled={!$feedPrevDate}
								onclick={() => { if ($feedPrevDate) $feedDate = $feedPrevDate; }}
								title="Previous day"
							>
								<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
								</svg>
							</button>
							<!-- Date label: clickable to jump to today when viewing a past date -->
							{#if isToday}
								<span class="w-[7.5rem] text-center text-xs font-medium text-surface-200 whitespace-nowrap">
									{formatDateLabel($feedDate)}
								</span>
							{:else}
								<button
									class="group/today w-[7.5rem] text-center text-xs font-medium whitespace-nowrap rounded-md px-2 py-1 transition-colors text-surface-200 hover:text-laya-orange hover:bg-laya-orange/10"
									onclick={() => ($feedDate = localToday())}
									title="Jump to today"
								>
									{formatDateLabel($feedDate)}
									<span class="block text-[9px] font-normal text-surface-500 group-hover/today:text-laya-orange/70 transition-colors">click for today</span>
								</button>
							{/if}
							<button
								class="rounded-md p-1.5 text-surface-400 transition-colors hover:bg-surface-800 hover:text-surface-200 disabled:opacity-30 disabled:hover:bg-transparent"
								disabled={!$feedNextDate}
								onclick={() => { if ($feedNextDate) $feedDate = $feedNextDate; }}
								title="Next day"
							>
								<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
								</svg>
							</button>
						</div>
					{/if}
				</div>
			{/if}
			<div class="flex-1"></div>

			<!-- Right: Global utilities -->
			<div class="flex items-center gap-1 ml-3">
				<!-- Chat -->
				<div class="group/tip relative">
					<button
						onclick={() => {
							if ($chatOpen) {
								chatOpen.set(false);
							} else {
								chatListOpen.set(true);
								chatOpen.set(true);
							}
						}}
						class="rounded-lg p-1.5 transition-colors hover:bg-surface-800
							{$chatOpen
								? 'bg-laya-orange/10 text-laya-orange'
								: 'text-surface-400 hover:text-laya-orange'}"
						aria-label="Chat with Laya"
					>
						<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
						</svg>
					</button>
					<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/tip:opacity-100">Chat</span>
				</div>

				<!-- Settings -->
				<div class="group/tip relative">
					<a
						href="/settings"
						class="block rounded-lg p-1.5 transition-colors hover:bg-surface-800
							{page.url.pathname.startsWith('/settings')
								? 'text-laya-orange'
								: 'text-surface-400 hover:text-laya-orange'}"
						aria-label="Settings"
					>
						<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
						</svg>
					</a>
					<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/tip:opacity-100">Settings</span>
				</div>

				<!-- Health badge -->
				<div class="group/tip relative">
					<a href="/status" class="block rounded-lg px-2 py-1.5 transition-colors hover:bg-surface-800" aria-label="System status">
						<HealthBadge />
					</a>
					<span class="pointer-events-none absolute right-0 top-full z-50 mt-1.5 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/tip:opacity-100">Status</span>
				</div>
			</div>
		</header>

		<!-- Main content — add right padding when chat sidebar is open so content isn't hidden behind it -->
		<main class="flex-1 overflow-auto p-4 transition-[padding] duration-250 {$chatOpen ? 'pr-[476px]' : ''}">
			{@render children()}
		</main>
	</div>

	<ChatSidebar />
{/if}
