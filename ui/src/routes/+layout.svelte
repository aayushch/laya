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
	import { getEngineUrl } from '$lib/config';
	import { fontScale } from '$lib/stores/fontScale';
	import { accessibleColors } from '$lib/stores/accessibleColors';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { cardDescriptions } from '$lib/stores/cardDescriptions';
	import { cardSize } from '$lib/stores/cardSize';
	import { feedViewMode } from '$lib/stores/feedView';
	import { fly } from 'svelte/transition';
	import { budgetPaused, loadBudgetStatus, handleBudgetWsMessage, costAmount, budgetLabel, budgetRatio } from '$lib/stores/budget';
	import { feedFilters, loadFeedFilters, saveFeedFilters, filtersLoaded, feedDate, feedPrevDate, feedNextDate, localToday } from '$lib/stores/feedFilters';
	import { spaces, loadSpaces } from '$lib/stores/spaces';
	import { compose } from '$lib/stores/compose';
	import ComposeModal from '$lib/components/egress/ComposeModal.svelte';
	import { agentDialog } from '$lib/stores/agentDialog';
	import { summaryModalOpen } from '$lib/stores/summaryModal';
	import { recentDrawerOpen } from '$lib/stores/recentCards';
	import { triggerSearchFocus } from '$lib/stores/searchFocus';
	import RunAgentModal from '$lib/components/agent/RunAgentModal.svelte';
	import UpdateBanner from '$lib/components/UpdateBanner.svelte';
	import Titlebar from '$lib/components/Titlebar.svelte';
	import { checkForUpdate } from '$lib/stores/updater';
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
	// Responsive titlebar: two-stage collapse using collision detection.
	// Stage 1: nav links collapse to hamburger when they overlap the center date panel.
	// Stage 2: date panel collapses to a calendar button when it overlaps the right controls.
	let navCollapsed = $state(false);
	let navMenuOpen = $state(false);
	let navCollapseWidth = 0;

	let dateCollapsed = $state(false);
	let dateMenuOpen = $state(false);
	let dateCollapseWidth = 0;

	let toasts = $state<Array<{id: string; message: string; variant: 'info' | 'warning'}>>([]);

	function addToast(message: string, variant: 'info' | 'warning' = 'info') {
		const id = crypto.randomUUID();
		toasts = [...toasts, { id, message, variant }];
		setTimeout(() => {
			toasts = toasts.filter(t => t.id !== id);
		}, 5000);
	}

	$effect(() => {
		function check() {
			const center = document.querySelector('[data-titlebar-center]');

			// Stage 1: nav vs center collision
			if (!navCollapsed) {
				if (center) {
					const nav = document.querySelector('[data-titlebar-nav]');
					if (nav) {
						const navRight = nav.getBoundingClientRect().right;
						const centerLeft = center.getBoundingClientRect().left;
						if (navRight + 8 >= centerLeft) {
							navCollapseWidth = window.innerWidth;
							navCollapsed = true;
							navMenuOpen = false;
						}
					}
				}
			} else {
				// Uncollapse nav only based on window width (not center existence).
				// When nav uncollapses, date must also uncollapse since nav needs even more room.
				if (window.innerWidth > navCollapseWidth + 60) {
					navCollapsed = false;
					dateCollapsed = false;
				}
			}

			// Stage 2: nav overflow menu vs center collision (only when nav is collapsed, date isn't)
			if (navCollapsed && !dateCollapsed) {
				if (center) {
					const navMenu = document.querySelector('[data-titlebar-nav-menu]');
					if (navMenu) {
						const navMenuRight = navMenu.getBoundingClientRect().right;
						const centerLeft = center.getBoundingClientRect().left;
						if (navMenuRight + 8 >= centerLeft) {
							dateCollapseWidth = window.innerWidth;
							dateCollapsed = true;
							dateMenuOpen = false;
						}
					}
				}
			} else if (dateCollapsed && navCollapsed) {
				// Uncollapse date when window grows past date collapse point (nav stays collapsed)
				if (window.innerWidth > dateCollapseWidth + 60) {
					dateCollapsed = false;
				}
			}
		}
		const observer = new ResizeObserver(check);
		observer.observe(document.documentElement);
		return () => observer.disconnect();
	});

	// Close menus when navigating
	$effect(() => {
		page.url.pathname;
		navMenuOpen = false;
		dateMenuOpen = false;
	});

	// Set header/footer height CSS variables for chat sidebar positioning
	$effect(() => {
		document.documentElement.style.setProperty('--header-h', '38px');
		document.documentElement.style.setProperty('--footer-h', '33px');
	});

	// Apply theme to <html> so CSS [data-theme] selectors work globally
	$effect(() => {
		document.documentElement.setAttribute('data-theme', $theme);
	});

	// Apply font scale as CSS custom property on <html>
	$effect(() => {
		document.documentElement.style.setProperty('--laya-font-base', `${$fontScale}px`);
	});

	// Apply accessible color mode attribute on <html>
	$effect(() => {
		if ($accessibleColors) {
			document.documentElement.setAttribute('data-accessible-colors', '');
		} else {
			document.documentElement.removeAttribute('data-accessible-colors');
		}
	});

	// Apply reduced motion attribute on <html> — CSS overrides in app.css
	// key off this to neutralize transitions/animations globally.
	$effect(() => {
		if ($reducedMotion) {
			document.documentElement.setAttribute('data-reduced-motion', '');
		} else {
			document.documentElement.removeAttribute('data-reduced-motion');
		}
	});

	// Apply glass theme attribute on <html> for CSS-based glass styling
	$effect(() => {
		if ($glassTheme) {
			document.documentElement.setAttribute('data-glass-theme', '');
		} else {
			document.documentElement.removeAttribute('data-glass-theme');
		}
	});

	// Sync feed view mode to <html> for CSS-based mesh gradient switching
	$effect(() => {
		document.documentElement.setAttribute('data-feed-view', $feedViewMode);
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
		fetch(`${getEngineUrl()}/settings/setup-status`)
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

	// Check for app updates after startup
	$effect(() => {
		if (!$startupReady) return;
		const timer = setTimeout(checkForUpdate, 5000);
		return () => clearTimeout(timer);
	});

	// React to budget WebSocket messages
	$effect(() => {
		const msg = $lastMessage;
		if (msg && msg.type === 'budget_status') {
			handleBudgetWsMessage(msg as any);
		}
	});

	// Refresh cost data when pipeline produces new cards (implies LLM usage)
	$effect(() => {
		const msg = $lastMessage;
		if (msg && (msg.type === 'card_created' || msg.type === 'card_updated')) {
			loadBudgetStatus();
		}
	});

	// React to open_compose WebSocket events
	$effect(() => {
		const msg = $lastMessage;
		if (msg && msg.type === 'open_compose') {
			const payload = msg.payload as Record<string, unknown>;
			compose.openCompose(
				String(payload.platform ?? ''),
				(payload.action_type as 'reply' | 'compose' | 'comment' | 'forward') ?? 'compose',
				(payload.prefill as Record<string, unknown>) ?? {},
				payload.source_card_id ? String(payload.source_card_id) : undefined
			);
		}
	});

	// React to processing rule auto-disable events
	$effect(() => {
		const msg = $lastMessage;
		if (msg && msg.type === 'processing_rule_auto_disabled') {
			const p = msg.payload as { name: string; reason: string };
			addToast(`Rule "${p.name}" auto-disabled: ${p.reason}`, 'warning');
		}
	});

	// React to push notification events
	$effect(() => {
		const msg = $lastMessage;
		if (msg && msg.type === 'push_notification') {
			const p = msg.payload as { title: string; body: string };
			addToast(`${p.title}: ${p.body}`, 'info');
		}
	});

	onMount(() => {
		startHealthPolling();
		initWebSocket();

		// Selectively disable Tauri's default context menu to hide browser nav
		// (Back/Forward/Reload/Inspect Element), but allow it on text-interactive
		// elements and text selections so copy/paste still works.
		document.addEventListener('contextmenu', (e) => {
			const target = e.target as HTMLElement;
			const tag = target?.tagName;

			// Allow native context menu on text-interactive elements
			if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return;

			// Allow native context menu when text is selected (for copy)
			const selection = window.getSelection();
			if (selection && selection.toString().trim().length > 0) return;

			// Block browser nav context menu on everything else
			e.preventDefault();
		});

		// Keyboard shortcut: press 'c' (not in input/textarea) to open compose modal
		function handleComposeShortcut(e: KeyboardEvent) {
			if (e.key === 'c' && !e.metaKey && !e.ctrlKey && !e.altKey) {
				const tag = (e.target as HTMLElement)?.tagName;
				if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target as HTMLElement)?.isContentEditable) return;
				e.preventDefault();
				compose.openCompose('gmail', 'compose', {});
			}
		}
		document.addEventListener('keydown', handleComposeShortcut);

		// Keyboard shortcut: press 'a' (not in input/textarea) to open run agent dialog
		function handleAgentShortcut(e: KeyboardEvent) {
			if (e.key === 'a' && !e.metaKey && !e.ctrlKey && !e.altKey) {
				const tag = (e.target as HTMLElement)?.tagName;
				if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target as HTMLElement)?.isContentEditable) return;
				e.preventDefault();
				agentDialog.open();
			}
		}
		document.addEventListener('keydown', handleAgentShortcut);

		// Keyboard shortcut: press 's' (not in input/textarea) to toggle summary modal
		function handleSummaryShortcut(e: KeyboardEvent) {
			if (e.key === 's' && !e.metaKey && !e.ctrlKey && !e.altKey) {
				const tag = (e.target as HTMLElement)?.tagName;
				if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target as HTMLElement)?.isContentEditable) return;
				e.preventDefault();
				summaryModalOpen.update(v => !v);
			}
		}
		document.addEventListener('keydown', handleSummaryShortcut);

		// Keyboard shortcut: press 'l' (not in input/textarea) to toggle chat panel
		function handleChatShortcut(e: KeyboardEvent) {
			if (e.key === 'l' && !e.metaKey && !e.ctrlKey && !e.altKey) {
				const tag = (e.target as HTMLElement)?.tagName;
				if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target as HTMLElement)?.isContentEditable) return;
				e.preventDefault();
				e.stopImmediatePropagation();
				let current = false;
				chatOpen.subscribe(v => { current = v; })();
				if (current) {
					chatOpen.set(false);
				} else {
					chatListOpen.set(true);
					chatOpen.set(true);
				}
			}
		}
		document.addEventListener('keydown', handleChatShortcut);

		// Keyboard shortcut: press 'r' to toggle recent items drawer
		function handleRecentShortcut(e: KeyboardEvent) {
			if (e.key === 'r' && !e.metaKey && !e.ctrlKey && !e.altKey) {
				const tag = (e.target as HTMLElement)?.tagName;
				if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target as HTMLElement)?.isContentEditable) return;
				e.preventDefault();
				recentDrawerOpen.update(v => !v);
			}
		}
		document.addEventListener('keydown', handleRecentShortcut);

		// Keyboard shortcut: press 'b' to toggle bookmarks filter
		function handleBookmarkShortcut(e: KeyboardEvent) {
			if (e.key === 'b' && !e.metaKey && !e.ctrlKey && !e.altKey) {
				const tag = (e.target as HTMLElement)?.tagName;
				if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target as HTMLElement)?.isContentEditable) return;
				e.preventDefault();
				feedFilters.update(f => ({ ...f, showBookmarked: !f.showBookmarked }));
			}
		}
		document.addEventListener('keydown', handleBookmarkShortcut);

		// Keyboard shortcut: Cmd+F (macOS) / Ctrl+F to focus feed search box
		function handleSearchShortcut(e: KeyboardEvent) {
			if (e.key === 'f' && (e.metaKey || e.ctrlKey) && !e.altKey) {
				if (page.url.pathname === '/feed' || page.url.pathname === '/') {
					e.preventDefault();
					triggerSearchFocus();
				}
			}
		}
		document.addEventListener('keydown', handleSearchShortcut);

		// Keyboard shortcut: Cmd+S (macOS) / Ctrl+S to navigate to Coherence and focus search
		function handleCoherenceShortcut(e: KeyboardEvent) {
			if (e.key === 's' && (e.metaKey || e.ctrlKey) && !e.altKey) {
				e.preventDefault();
				goto('/coherence').then(() => {
					requestAnimationFrame(() => triggerSearchFocus());
				});
			}
		}
		document.addEventListener('keydown', handleCoherenceShortcut);

		// Keyboard shortcut: press 'p' to navigate to Pulse (feed)
		function handlePulseShortcut(e: KeyboardEvent) {
			if (e.key === 'p' && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey) {
				e.preventDefault();
				goto('/feed');
			}
		}
		document.addEventListener('keydown', handlePulseShortcut);

		// Keyboard shortcut: press 'o' to navigate to Omni
		function handleOmniShortcut(e: KeyboardEvent) {
			if (e.key === 'o' && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey) {
				e.preventDefault();
				goto('/omni');
			}
		}
		document.addEventListener('keydown', handleOmniShortcut);

		// Keyboard shortcut: Cmd+D (macOS) / Ctrl+D toggles card descriptions globally.
		// preventDefault stops the browser bookmark dialog (irrelevant in Tauri but
		// matters for web preview).
		function handleCardDescriptionsShortcut(e: KeyboardEvent) {
			if (e.key === 'd' && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey) {
				e.preventDefault();
				cardDescriptions.toggle();
			}
		}
		document.addEventListener('keydown', handleCardDescriptionsShortcut);

		// Keyboard shortcut: Cmd+Shift+D (macOS) / Ctrl+Shift+D toggles compact / relaxed card layout.
		// e.key with Shift held is uppercase 'D' on most layouts; check both for safety.
		function handleCardSizeShortcut(e: KeyboardEvent) {
			if ((e.key === 'd' || e.key === 'D') && (e.metaKey || e.ctrlKey) && e.shiftKey && !e.altKey) {
				e.preventDefault();
				cardSize.toggle();
			}
		}
		document.addEventListener('keydown', handleCardSizeShortcut);

		function handleNavBackShortcut(e: KeyboardEvent) {
			if (e.key === '[' && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey) {
				e.preventDefault();
				history.back();
			}
		}
		document.addEventListener('keydown', handleNavBackShortcut);

		function handleNavForwardShortcut(e: KeyboardEvent) {
			if (e.key === ']' && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey) {
				e.preventDefault();
				history.forward();
			}
		}
		document.addEventListener('keydown', handleNavForwardShortcut);

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
			document.removeEventListener('keydown', handleComposeShortcut);
			document.removeEventListener('keydown', handleAgentShortcut);
			document.removeEventListener('keydown', handleSummaryShortcut);
			document.removeEventListener('keydown', handleChatShortcut);
			document.removeEventListener('keydown', handleRecentShortcut);
			document.removeEventListener('keydown', handleBookmarkShortcut);
			document.removeEventListener('keydown', handleSearchShortcut);
			document.removeEventListener('keydown', handleCoherenceShortcut);
			document.removeEventListener('keydown', handlePulseShortcut);
			document.removeEventListener('keydown', handleOmniShortcut);
			document.removeEventListener('keydown', handleCardDescriptionsShortcut);
			document.removeEventListener('keydown', handleCardSizeShortcut);
			document.removeEventListener('keydown', handleNavBackShortcut);
			document.removeEventListener('keydown', handleNavForwardShortcut);
			clearTimeout(midnightTimer);
			stopHealthPolling();
			closeWebSocket();
		};
	});

</script>

<Titlebar>
	{#snippet nav()}
		{#if navCollapsed}
			<!-- Overflow menu for narrow windows -->
			<div data-titlebar-nav-menu class="relative">
				<button
					onclick={() => (navMenuOpen = !navMenuOpen)}
					class="rounded-md p-1 text-surface-400 transition-colors hover:text-surface-200 hover:bg-surface-800"
					aria-label="Navigation menu"
				>
					<svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h16M4 18h16" />
					</svg>
				</button>
				{#if navMenuOpen}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div class="fixed inset-0 z-[9998]" onclick={() => (navMenuOpen = false)} onkeydown={() => {}}></div>
					<nav class="absolute left-0 top-full z-[9999] mt-1 flex flex-col rounded-lg border border-surface-700 bg-surface-800 py-1 shadow-lg">
						<a
							href="/feed"
							class="whitespace-nowrap px-4 py-1.5 text-xs font-medium transition-colors
								{isFeedRoute
									? 'bg-laya-orange/10 text-laya-orange'
									: 'text-surface-400 hover:text-surface-200 hover:bg-surface-700'}"
						>Pulse</a>
						<a
							href="/omni"
							class="whitespace-nowrap px-4 py-1.5 text-xs font-medium transition-colors
								{page.url.pathname.startsWith('/omni')
									? 'bg-laya-orange/10 text-laya-orange'
									: 'text-surface-400 hover:text-surface-200 hover:bg-surface-700'}"
						>Omni</a>
						<a
							href="/coherence"
							class="whitespace-nowrap px-4 py-1.5 text-xs font-medium transition-colors
								{page.url.pathname.startsWith('/coherence')
									? 'bg-laya-orange/10 text-laya-orange'
									: 'text-surface-400 hover:text-surface-200 hover:bg-surface-700'}"
						>Coherence<sup class="text-[7px] ml-0.5 opacity-60 tracking-wider">BETA</sup></a>
					</nav>
				{/if}
			</div>
		{:else}
			<!-- Inline nav for normal widths -->
			<nav data-titlebar-nav class="flex items-center gap-0.5">
				<a
					href="/feed"
					class="rounded-md px-2.5 py-1 text-xs font-medium transition-colors
						{isFeedRoute
							? 'bg-laya-orange/10 text-laya-orange'
							: 'text-surface-400 hover:text-surface-200 hover:bg-surface-800'}"
				>Pulse</a>
				<a
					href="/omni"
					class="rounded-md px-2.5 py-1 text-xs font-medium transition-colors
						{page.url.pathname.startsWith('/omni')
							? 'bg-laya-orange/10 text-laya-orange'
							: 'text-surface-400 hover:text-surface-200 hover:bg-surface-800'}"
				>Omni</a>
				<a
					href="/coherence"
					class="rounded-md px-2.5 py-1 text-xs font-medium transition-colors
						{page.url.pathname.startsWith('/coherence')
							? 'bg-laya-orange/10 text-laya-orange'
							: 'text-surface-400 hover:text-surface-200 hover:bg-surface-800'}"
				>Coherence<sup class="text-[7px] ml-0.5 opacity-60 tracking-wider">BETA</sup></a>
			</nav>
		{/if}
	{/snippet}
	{#snippet center()}
		{#if isFeedRoute}
			{#if dateCollapsed}
				<!-- Collapsed date: calendar icon button with dropdown -->
				<div class="relative">
					<button
						onclick={() => (dateMenuOpen = !dateMenuOpen)}
						class="rounded-md p-1 text-surface-400 transition-colors hover:text-surface-200 hover:bg-surface-800"
						aria-label="Date navigation"
						title={formatDateLabel($feedDate)}
					>
						<svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5" />
						</svg>
					</button>
					{#if dateMenuOpen}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div class="fixed inset-0 z-[9998]" onclick={() => (dateMenuOpen = false)} onkeydown={() => {}}></div>
						<div class="absolute left-1/2 -translate-x-1/2 top-full z-[9999] mt-1 rounded-lg border border-surface-700 bg-surface-800 p-2 shadow-lg">
							{#if $feedFilters.showBookmarked}
								<div class="flex items-center gap-1.5 px-2 py-1">
									<svg class="h-3.5 w-3.5 text-laya-orange" fill="currentColor" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
									</svg>
									<span class="text-xs font-medium text-laya-orange whitespace-nowrap">Bookmarked</span>
								</div>
							{:else if $feedFilters.showRelated}
								<div class="flex items-center gap-1.5 px-2 py-1">
									<svg class="h-3.5 w-3.5 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
									</svg>
									<span class="text-xs font-medium text-laya-orange whitespace-nowrap">Related</span>
								</div>
							{:else if $feedFilters.showAllDaysSearch}
								<div class="flex items-center gap-1.5 px-2 py-1">
									<svg class="h-3.5 w-3.5 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
									</svg>
									<span class="text-xs font-medium text-laya-orange whitespace-nowrap">All days</span>
								</div>
							{:else}
								<div class="flex items-center gap-1">
									<button
										class="rounded-md p-1.5 text-surface-400 transition-colors hover:bg-surface-700 hover:text-surface-200 disabled:opacity-30"
										disabled={!$feedPrevDate}
										onclick={() => { if ($feedPrevDate) $feedDate = $feedPrevDate; }}
										title="Previous day"
									>
										<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
											<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
										</svg>
									</button>
									{#if isToday}
										<span class="w-[7.5rem] text-center text-xs font-medium text-surface-200 whitespace-nowrap">
											{formatDateLabel($feedDate)}
										</span>
									{:else}
										<button
											class="w-[7.5rem] text-center text-xs font-medium whitespace-nowrap rounded-md px-2 py-1 transition-colors text-surface-200 hover:text-laya-orange hover:bg-laya-orange/10"
											onclick={() => { $feedDate = localToday(); dateMenuOpen = false; }}
											title="Jump to today"
										>
											{formatDateLabel($feedDate)}
										</button>
									{/if}
									<button
										class="rounded-md p-1.5 text-surface-400 transition-colors hover:bg-surface-700 hover:text-surface-200 disabled:opacity-30"
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
				</div>
			{:else if $feedFilters.showBookmarked}
				<div data-titlebar-center class="flex items-center gap-1.5">
					<svg class="h-3.5 w-3.5 text-laya-orange" fill="currentColor" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
					</svg>
					<span class="text-xs font-medium text-laya-orange whitespace-nowrap">Bookmarked</span>
				</div>
			{:else if $feedFilters.showRelated}
				<div data-titlebar-center class="flex items-center gap-1.5">
					<svg class="h-3.5 w-3.5 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
					</svg>
					<span class="text-xs font-medium text-laya-orange whitespace-nowrap">Related</span>
				</div>
			{:else if $feedFilters.showAllDaysSearch}
				<div data-titlebar-center class="flex items-center gap-1.5">
					<svg class="h-3.5 w-3.5 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
					</svg>
					<span class="text-xs font-medium text-laya-orange whitespace-nowrap">All days</span>
				</div>
			{:else}
				<div data-titlebar-center class="flex items-center gap-1">
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
		{/if}
	{/snippet}
	{#snippet right()}
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
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
				</svg>
			</button>
			<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium opacity-0 transition-opacity duration-75 group-hover/tip:opacity-100">Chat</span>
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
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
				</svg>
			</a>
			<span class="pointer-events-none absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium opacity-0 transition-opacity duration-75 group-hover/tip:opacity-100">Settings</span>
		</div>

		<!-- Health badge -->
		<div class="group/tip relative">
			<a href="/status" class="block rounded-lg px-1.5 py-1 transition-colors hover:bg-surface-800" aria-label="System status">
				<HealthBadge />
			</a>
			<span class="pointer-events-none absolute right-0 top-full z-50 mt-1.5 whitespace-nowrap rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium opacity-0 transition-opacity duration-75 group-hover/tip:opacity-100">Status</span>
		</div>
	{/snippet}
</Titlebar>

{#if $needsSetup || !$startupReady}
	<StartupScreen />
{:else if isSetupRoute}
	{@render children()}
{:else}
	<div class="flex h-screen flex-col {$glassTheme ? 'bg-transparent' : 'bg-surface-900'} pt-[38px] text-surface-50">
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

		<!-- Update available banner -->
		<UpdateBanner />

		<!-- Main content — add right padding when chat sidebar is open so content isn't hidden behind it -->
		<main class="flex-1 overflow-auto p-4 {$chatOpen ? 'pr-[476px]' : ''}">
			{#key page.url.pathname}
				<div
					class="h-full"
					in:fly={{ y: $reducedMotion ? 0 : 6, duration: $reducedMotion ? 0 : 150, opacity: 0 }}
				>
					{@render children()}
				</div>
			{/key}
		</main>

		<!-- Footer -->
		<footer class="relative flex items-center justify-between border-t border-surface-700/60 bg-surface-900/95 px-5 py-1.5 text-[11px] text-surface-500 backdrop-blur-sm">
			<!-- Left: Date widget -->
			<div class="flex items-center gap-2 text-surface-600">
				<span class="tabular-nums">{new Date().toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}</span>
				<span class="text-surface-700">|</span>
				<a href="/legal" class="text-surface-600 transition-colors hover:text-surface-300">Terms &amp; License</a>
			</div>
			<!-- Center: AI disclaimer -->
			<div class="pointer-events-none absolute left-1/2 top-1/2 hidden -translate-x-1/2 -translate-y-1/2 whitespace-nowrap text-surface-600 md:block">
				Content is generated by AI and may contain mistakes.
			</div>
			<!-- Right: Cost widget -->
			<div class="flex items-center gap-4">
				{#if $costAmount}
					<div class="flex items-center gap-0.5">
						<!-- Cost amount → status page breakdown -->
						<a
							href="/status#cost"
							class="rounded-md px-1.5 py-0.5 font-medium tabular-nums transition-colors hover:bg-surface-800 {$budgetPaused ? 'text-red-400 hover:text-red-300' : $budgetRatio != null && $budgetRatio >= 0.75 ? 'text-amber-400 hover:text-amber-300' : 'text-surface-400 hover:text-surface-200'}"
							title="LLM cost this month — click for breakdown"
						>
							{$costAmount}
						</a>
						<!-- Budget limit + bar → settings cost control -->
						{#if $budgetLabel}
							<a
								href="/settings?tab=models&section=cost-control"
								class="flex items-center gap-1.5 rounded-md px-1.5 py-0.5 font-medium tabular-nums text-surface-500 transition-colors hover:bg-surface-800 hover:text-surface-300"
								title="Monthly budget — click to manage"
							>
								{$budgetLabel}
								{#if $budgetRatio != null}
									<div class="h-1 w-10 rounded-full bg-surface-700 overflow-hidden">
										<div
											class="h-full rounded-full transition-all duration-300 {$budgetRatio >= 1 ? 'bg-red-400' : $budgetRatio >= 0.75 ? 'bg-amber-400' : 'bg-emerald-400'}"
											style="width: {$budgetRatio * 100}%"
										></div>
									</div>
								{/if}
							</a>
						{/if}
					</div>
				{/if}
			</div>
		</footer>
	</div>

	<ChatSidebar />
	<ComposeModal />
	<RunAgentModal />

	{#if toasts.length > 0}
		<div class="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
			{#each toasts as toast (toast.id)}
				<div class="rounded-lg border px-4 py-3 text-sm shadow-lg backdrop-blur-sm transition-all
					{toast.variant === 'warning'
						? 'border-amber-700/50 bg-amber-950/90 text-amber-200'
						: 'border-surface-600/50 bg-surface-800/90 text-surface-200'}">
					{toast.message}
				</div>
			{/each}
		</div>
	{/if}
{/if}
