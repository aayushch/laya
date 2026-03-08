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
	import { feedFilters, loadFeedFilters, saveFeedFilters, feedDate, feedPrevDate, feedNextDate } from '$lib/stores/feedFilters';
	import { onMount } from 'svelte';

	function formatDateLabel(dateStr: string): string {
		const today = new Date().toISOString().slice(0, 10);
		const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
		if (dateStr === today) return 'Today';
		if (dateStr === yesterday) return 'Yesterday';
		return new Date(dateStr + 'T00:00:00').toLocaleDateString(undefined, {
			weekday: 'short',
			month: 'short',
			day: 'numeric'
		});
	}

	const isToday = $derived($feedDate === new Date().toISOString().slice(0, 10));

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

	// Persist when filters change
	$effect(() => {
		$feedFilters;
		saveFeedFilters();
	});

	// Status/priority filter dropdowns
	let statusDropdownOpen = $state(false);
	let priorityDropdownOpen = $state(false);

	function closeDropdowns(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (!target.closest('.filter-dropdown')) {
			statusDropdownOpen = false;
			priorityDropdownOpen = false;
		}
	}

	onMount(async () => {
		startHealthPolling();
		initWebSocket();
		loadFeedFilters();

		document.addEventListener('click', closeDropdowns);

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
			document.removeEventListener('click', closeDropdowns);
		};
	});

	const activeStatusCount = $derived($feedFilters.statusFilters.length);
	const activePriorityCount = $derived($feedFilters.priorityFilters.length);
	const hasActiveFilters = $derived(activeStatusCount > 0 || activePriorityCount > 0 || $feedFilters.showArchived);
</script>

{#if isSetupRoute}
	{@render children()}
{:else}
	<div class="flex h-screen flex-col bg-surface-900 text-surface-50">
		<!-- Header -->
		<header bind:this={headerEl} class="relative z-50 flex items-center justify-between border-b border-surface-700 bg-surface-900/95 px-5 py-2.5 backdrop-blur-sm">
			<!-- Left: Logo -->
			<a href="/feed" class="flex items-center gap-2 mr-4">
				<h1 class="text-xl font-bold tracking-wide text-laya-orange">Laya</h1>
			</a>

			<!-- Center: Feed controls (only on feed route) -->
			{#if isFeedRoute}
				<div class="flex flex-1 items-center gap-3 min-w-0">
					<!-- Date navigation -->
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
								onclick={() => ($feedDate = new Date().toISOString().slice(0, 10))}
							>Today</button>
						{/if}
					</div>

					<!-- Divider -->
					<div class="h-5 w-px bg-surface-700"></div>

					<!-- Sort -->
					<div class="flex items-center gap-1.5 rounded-lg border border-surface-700 bg-surface-800/60 px-2 py-1">
						<svg class="h-3.5 w-3.5 text-surface-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4h13M3 8h9m-9 4h6m4 0l4-4m0 0l4 4m-4-4v12" />
						</svg>
						<select
							bind:value={$feedFilters.sortBy}
							class="bg-transparent text-xs text-surface-200 outline-none cursor-pointer appearance-none pr-4"
							style="background-image: url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%2712%27 height=%2712%27 viewBox=%270 0 24 24%27 fill=%27none%27 stroke=%27%23888%27 stroke-width=%272%27%3E%3Cpath d=%27M6 9l6 6 6-6%27/%3E%3C/svg%3E'); background-repeat: no-repeat; background-position: right 0 center;"
						>
							<option value="newest">Newest</option>
							<option value="priority">Priority</option>
							<option value="category">Category</option>
							<option value="platform">Source</option>
						</select>
					</div>

					<!-- Divider -->
					<div class="h-5 w-px bg-surface-700"></div>

					<!-- Status filter dropdown -->
					<div class="filter-dropdown relative">
						<button
							class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors
								{activeStatusCount > 0
									? 'border-laya-orange/40 bg-laya-orange/10 text-laya-orange'
									: 'border-surface-700 bg-surface-800/60 text-surface-400 hover:text-surface-200 hover:border-surface-600'}"
							onclick={() => { statusDropdownOpen = !statusDropdownOpen; priorityDropdownOpen = false; }}
						>
							<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
							</svg>
							Status
							{#if activeStatusCount > 0}
								<span class="flex h-4 w-4 items-center justify-center rounded-full bg-laya-orange/20 text-[10px] font-bold text-laya-orange">{activeStatusCount}</span>
							{/if}
						</button>
						{#if statusDropdownOpen}
							<div class="absolute left-0 top-full z-50 mt-1.5 w-44 rounded-lg border border-surface-600 bg-surface-800 p-1.5 shadow-xl shadow-black/30">
								{#each [['pending', 'Pending'], ['approved', 'Approved'], ['dismissed', 'Dismissed'], ['archived', 'Archived']] as [value, label]}
									<button
										class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs transition-colors hover:bg-surface-700
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
						{/if}
					</div>

					<!-- Priority filter dropdown -->
					<div class="filter-dropdown relative">
						<button
							class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors
								{activePriorityCount > 0
									? 'border-laya-orange/40 bg-laya-orange/10 text-laya-orange'
									: 'border-surface-700 bg-surface-800/60 text-surface-400 hover:text-surface-200 hover:border-surface-600'}"
							onclick={() => { priorityDropdownOpen = !priorityDropdownOpen; statusDropdownOpen = false; }}
						>
							<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 21v-4m4 4v-8m4 8v-6m4 6V9m4 12V3" />
							</svg>
							Priority
							{#if activePriorityCount > 0}
								<span class="flex h-4 w-4 items-center justify-center rounded-full bg-laya-orange/20 text-[10px] font-bold text-laya-orange">{activePriorityCount}</span>
							{/if}
						</button>
						{#if priorityDropdownOpen}
							<div class="absolute left-0 top-full z-50 mt-1.5 w-44 rounded-lg border border-surface-600 bg-surface-800 p-1.5 shadow-xl shadow-black/30">
								{#each [['CRITICAL', 'Critical'], ['HIGH', 'High'], ['MEDIUM', 'Medium'], ['LOW', 'Low']] as [value, label]}
									<button
										class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs transition-colors hover:bg-surface-700
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
						{/if}
					</div>

					<!-- Archived toggle -->
					<button
						class="flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors
							{$feedFilters.showArchived
								? 'border-laya-orange/40 bg-laya-orange/10 text-laya-orange'
								: 'border-surface-700 bg-surface-800/60 text-surface-400 hover:text-surface-200 hover:border-surface-600'}"
						onclick={() => ($feedFilters.showArchived = !$feedFilters.showArchived)}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
						</svg>
						Archived
					</button>

					<!-- Clear all filters -->
					{#if hasActiveFilters}
						<button
							class="rounded-md px-2 py-1 text-[10px] font-medium text-surface-500 transition-colors hover:text-surface-300"
							onclick={() => {
								$feedFilters.statusFilters = [];
								$feedFilters.priorityFilters = [];
								$feedFilters.showArchived = false;
							}}
						>
							Clear all
						</button>
					{/if}
				</div>
			{:else}
				<div class="flex-1"></div>
			{/if}

			<!-- Right: Actions -->
			<div class="flex items-center gap-1 ml-3">
				<!-- Chat -->
				<button
					onclick={() => chatOpen.update((v) => !v)}
					class="rounded-lg p-1.5 transition-colors hover:bg-surface-800
						{$chatOpen
							? 'bg-laya-orange/10 text-laya-orange'
							: 'text-surface-400 hover:text-laya-orange'}"
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
				<a href="/status" class="rounded-lg px-2 py-1.5 transition-colors hover:bg-surface-800" title="System status">
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
