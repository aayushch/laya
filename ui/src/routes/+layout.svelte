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

	function toggleFilter(arr: string[], value: string): string[] {
		return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value];
	}

	// Persist when filters change (only after initial load to avoid overwriting saved prefs with defaults)
	$effect(() => {
		$feedFilters;
		if (filtersLoaded()) saveFeedFilters();
	});

	// Filter popover
	let filterPopoverOpen = $state(false);

	function closeDropdowns(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (!target.isConnected) return;
		if (!target.closest('.filter-dropdown')) {
			filterPopoverOpen = false;
		}
	}

	const activeSpaceCount = $derived($feedFilters.spaceFilter.length);

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
		document.addEventListener('click', closeDropdowns);

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
			document.removeEventListener('click', closeDropdowns);
		};
	});

	const activeStatusCount = $derived($feedFilters.statusFilters.length);
	const activePriorityCount = $derived($feedFilters.priorityFilters.length);
	const hasActiveFilters = $derived(activeStatusCount > 0 || activePriorityCount > 0 || $feedFilters.showArchived || $feedFilters.showBookmarked || activeSpaceCount > 0);
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
				<a href="/settings" class="ml-1 text-xs font-medium text-red-400 underline underline-offset-2 hover:text-red-300">Manage</a>
			</div>
		{/if}

		<!-- Header -->
		<header bind:this={headerEl} class="relative z-50 flex items-center justify-between border-b border-surface-700 bg-surface-900/95 px-5 py-2.5 backdrop-blur-sm">
			<!-- Left: Logo -->
			<a href="/feed" class="flex items-center gap-2 mr-4">
				<h1 class="text-xl font-bold tracking-wide text-laya-orange">Laya</h1>
			</a>

			<!-- Center: Date navigation (only on feed route) -->
			{#if isFeedRoute}
				<div class="flex flex-1 items-center gap-3 min-w-0">
					<!-- Date / Bookmark mode -->
					<div class="flex items-center justify-center shrink-0">
						{#if $feedFilters.showBookmarked}
							<div class="flex items-center gap-1.5">
								<svg class="h-3.5 w-3.5 text-laya-orange" fill="currentColor" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
								</svg>
								<span class="text-xs font-medium text-laya-orange whitespace-nowrap">Bookmarked</span>
							</div>
						{:else}
							<div class="flex items-center gap-0.5">
								<button
									class="rounded p-1 text-surface-400 transition-colors hover:bg-surface-800 hover:text-surface-200 disabled:opacity-30 disabled:hover:bg-transparent"
									disabled={!$feedPrevDate}
									onclick={() => { if ($feedPrevDate) $feedDate = $feedPrevDate; }}
									title="Previous day"
								>
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
									</svg>
								</button>
								<span class="min-w-[4.5rem] text-center text-xs font-medium text-surface-200">
									{formatDateLabel($feedDate)}
								</span>
								<button
									class="rounded p-1 text-surface-400 transition-colors hover:bg-surface-800 hover:text-surface-200 disabled:opacity-30 disabled:hover:bg-transparent"
									disabled={!$feedNextDate}
									onclick={() => { if ($feedNextDate) $feedDate = $feedNextDate; }}
									title="Next day"
								>
									<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
									</svg>
								</button>
								{#if !isToday}
									<button
										class="ml-0.5 rounded-full bg-laya-orange/15 px-2 py-0.5 text-[10px] font-medium text-laya-orange transition-colors hover:bg-laya-orange/25"
										onclick={() => ($feedDate = localToday())}
									>Today</button>
								{/if}
							</div>
						{/if}
					</div>
				</div>
			{:else}
				<div class="flex-1"></div>
			{/if}

			<!-- Right: Actions -->
			<div class="flex items-center gap-1 ml-3">
				<!-- Filter popover -->
				{#if isFeedRoute}
					<div class="filter-dropdown relative">
						<div class="group/tip relative">
						<button
							onclick={() => (filterPopoverOpen = !filterPopoverOpen)}
							class="relative rounded-lg p-1.5 transition-colors hover:bg-surface-800
								{hasActiveFilters
									? 'bg-laya-orange/10 text-laya-orange'
									: 'text-surface-400 hover:text-laya-orange'}"
							aria-label="Filters"
						>
							<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
							</svg>
							{#if hasActiveFilters}
								<span class="absolute -top-0.5 -right-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-laya-orange text-[8px] font-bold text-surface-900">{activeStatusCount + activePriorityCount + activeSpaceCount + ($feedFilters.showArchived ? 1 : 0) + ($feedFilters.showBookmarked ? 1 : 0)}</span>
							{/if}
						</button>
						{#if !filterPopoverOpen}
							<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/tip:opacity-100">Filters</span>
						{/if}
						</div>

						{#if filterPopoverOpen}
							<div class="absolute right-0 top-full z-50 mt-1.5 w-64 rounded-xl border border-surface-600 bg-surface-800 p-3 shadow-xl shadow-black/30">
								<!-- Sort -->
								<div class="mb-3">
									<div class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Sort</div>
									<div class="flex items-center gap-1.5">
										<div class="flex flex-1 items-center gap-1.5 rounded-lg border border-surface-700 bg-surface-900/60 px-2 py-1">
											<svg class="h-3.5 w-3.5 text-surface-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4h13M3 8h9m-9 4h6m4 0l4-4m0 0l4 4m-4-4v12" />
											</svg>
											<select
												bind:value={$feedFilters.sortBy}
												class="flex-1 bg-transparent text-xs text-surface-200 outline-none cursor-pointer appearance-none pr-4"
												style="background-image: url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%2712%27 height=%2712%27 viewBox=%270 0 24 24%27 fill=%27none%27 stroke=%27%23888%27 stroke-width=%272%27%3E%3Cpath d=%27M6 9l6 6 6-6%27/%3E%3C/svg%3E'); background-repeat: no-repeat; background-position: right 0 center;"
											>
												<option value="newest">Newest</option>
												<option value="priority">Priority</option>
												<option value="status">Status</option>
												<option value="persona">Persona</option>
												<option value="category">Category</option>
												<option value="platform">Source</option>
											</select>
										</div>
										<button
											aria-label="Toggle sort direction"
											class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-surface-700 bg-surface-900/60 text-surface-400 transition-colors hover:bg-surface-700 hover:text-surface-200"
											onclick={() => ($feedFilters.sortAsc = !$feedFilters.sortAsc)}
										>
											<svg class="h-3 w-3 transition-transform {$feedFilters.sortAsc ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 14l-7 7m0 0l-7-7m7 7V3" />
											</svg>
										</button>
									</div>
								</div>

								<!-- Workspace -->
								{#if $spaces.length > 1}
									<div class="mb-3">
										<div class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Workspace</div>
										<div class="space-y-0.5">
											{#each $spaces as space}
												<button
													class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors hover:bg-surface-700
														{$feedFilters.spaceFilter.includes(space.space_id) ? 'text-laya-orange' : 'text-surface-300'}"
													onclick={() => ($feedFilters.spaceFilter = toggleFilter($feedFilters.spaceFilter, space.space_id))}
												>
													<span class="flex h-4 w-4 items-center justify-center rounded border {$feedFilters.spaceFilter.includes(space.space_id) ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-600'}">
														{#if $feedFilters.spaceFilter.includes(space.space_id)}
															<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
																<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
															</svg>
														{/if}
													</span>
													<span class="h-2 w-2 rounded-full shrink-0" style="background-color: {space.color}"></span>
													{space.name}
												</button>
											{/each}
										</div>
									</div>
								{/if}

								<!-- Status -->
								<div class="mb-3">
									<div class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Status</div>
									<div class="space-y-0.5">
										{#each [['pending', 'Processing'], ['ready', 'Ready'], ['requires_approval', 'Needs Approval'], ['agent_running', 'Running'], ['failed', 'Failed'], ['done', 'Done'], ['dismissed', 'Dismissed']] as [value, label]}
											<button
												class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors hover:bg-surface-700
													{$feedFilters.statusFilters.includes(value) ? 'text-laya-orange' : 'text-surface-300'}"
												onclick={() => ($feedFilters.statusFilters = toggleFilter($feedFilters.statusFilters, value))}
											>
												<span class="flex h-4 w-4 items-center justify-center rounded border {$feedFilters.statusFilters.includes(value) ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-600'}">
													{#if $feedFilters.statusFilters.includes(value)}
														<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
															<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
														</svg>
													{/if}
												</span>
												{label}
											</button>
										{/each}
									</div>
								</div>

								<!-- Priority -->
								<div class="mb-3">
									<div class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-surface-500">Priority</div>
									<div class="space-y-0.5">
										{#each [['CRITICAL', 'Critical'], ['HIGH', 'High'], ['MEDIUM', 'Medium'], ['LOW', 'Low']] as [value, label]}
											<button
												class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors hover:bg-surface-700
													{$feedFilters.priorityFilters.includes(value) ? 'text-laya-orange' : 'text-surface-300'}"
												onclick={() => ($feedFilters.priorityFilters = toggleFilter($feedFilters.priorityFilters, value))}
											>
												<span class="flex h-4 w-4 items-center justify-center rounded border {$feedFilters.priorityFilters.includes(value) ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-600'}">
													{#if $feedFilters.priorityFilters.includes(value)}
														<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
															<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
														</svg>
													{/if}
												</span>
												{label}
											</button>
										{/each}
									</div>
								</div>

								<!-- Toggles -->
								<div class="mb-2 space-y-0.5">
									<button
										class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors hover:bg-surface-700
											{$feedFilters.showArchived ? 'text-laya-orange' : 'text-surface-300'}"
										onclick={() => ($feedFilters.showArchived = !$feedFilters.showArchived)}
									>
										<span class="flex h-4 w-4 items-center justify-center rounded border {$feedFilters.showArchived ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-600'}">
											{#if $feedFilters.showArchived}
												<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
													<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
												</svg>
											{/if}
										</span>
										Show Archived
									</button>
								</div>

								<!-- Clear all -->
								{#if hasActiveFilters}
									<div class="border-t border-surface-700 pt-2">
										<button
											class="w-full rounded-md px-2 py-1 text-[11px] font-medium text-surface-500 transition-colors hover:text-surface-300 hover:bg-surface-700"
											onclick={() => {
												$feedFilters.statusFilters = [];
												$feedFilters.priorityFilters = [];
												$feedFilters.showArchived = false;
												$feedFilters.showBookmarked = false;
												$feedFilters.spaceFilter = [];
											}}
										>
											Clear all filters
										</button>
									</div>
								{/if}
							</div>
						{/if}
					</div>

					<!-- Bookmarks toggle -->
					<div class="group/tip relative">
						<button
							onclick={() => ($feedFilters.showBookmarked = !$feedFilters.showBookmarked)}
							class="rounded-lg p-1.5 transition-colors hover:bg-surface-800
								{$feedFilters.showBookmarked
									? 'bg-laya-orange/10 text-laya-orange'
									: 'text-surface-400 hover:text-laya-orange'}"
							aria-label="Bookmarks"
						>
							<svg class="h-5 w-5" fill={$feedFilters.showBookmarked ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
							</svg>
						</button>
						<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/tip:opacity-100">{$feedFilters.showBookmarked ? 'Exit Bookmarks' : 'Show Bookmarked Cards'}</span>
					</div>
				{/if}

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
					<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/tip:opacity-100">Chat with Laya</span>
				</div>

				<!-- Settings gear -->
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
					<span class="pointer-events-none absolute right-0 top-full z-50 mt-1.5 whitespace-nowrap rounded-md border border-laya-orange/20 bg-surface-800 px-2 py-1 text-[10px] font-medium text-laya-orange opacity-0 shadow-lg transition-opacity duration-75 group-hover/tip:opacity-100">System Status</span>
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
