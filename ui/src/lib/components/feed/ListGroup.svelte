<script lang="ts">
	import type { CardGroup, ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { slide } from 'svelte/transition';
	import { cardColors } from '$lib/stores/cardColors';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { portal } from '$lib/actions/portal';
	import ListRow from './ListRow.svelte';
	import { platformDotColor } from '$lib/utils/cardVisuals';

	let {
		group,
		onselect,
		onselectgroup,
		ondelete,
		onlink,
		selectedCardId = '',
		selectedEntityId = '',
		scrollToCardId = null,
		bulkSelectedIds,
		onbulktoggle,
		onbulktogglegroup,
		hasSelection = false,
		lastViewedCardId = '',
		lastViewedEntityId = ''
	}: {
		group: CardGroup;
		onselect: (card: ActionCard) => void;
		onselectgroup?: (group: CardGroup) => void;
		ondelete?: (cardId: string) => void;
		onlink?: (group: CardGroup) => void;
		selectedCardId?: string;
		selectedEntityId?: string;
		scrollToCardId?: string | null;
		bulkSelectedIds?: Set<string>;
		onbulktoggle?: (cardId: string, event: MouseEvent) => void;
		onbulktogglegroup?: (cardIds: string[], selected: boolean) => void;
		hasSelection?: boolean;
		lastViewedCardId?: string;
		lastViewedEntityId?: string;
	} = $props();

	let expanded = $state(false);
	let groupMenuOpen = $state(false);
	let menuEl: HTMLElement | undefined = $state();
	let wrapperEl: HTMLElement | undefined = $state();
	let bulkActionRunning = $state(false);
	// Listen for programmatic 'expand' event (e.g. "Show all cards" in GroupSummaryDetail)
	$effect(() => {
		if (!wrapperEl) return;
		const handler = () => { expanded = true; };
		wrapperEl.addEventListener('expand', handler);
		return () => wrapperEl?.removeEventListener('expand', handler);
	});

	// Extract subject ID from entity_id (e.g., "jira:ticket:FERR-1056" → "FERR-1056")
	const subjectId = $derived(group.entity_id?.includes(':') ? group.entity_id.split(':').pop() : group.entity_id);

	const hasBookmark = $derived(group.cards.some((c) => c.bookmarked_at));
	const isGroupSelected = $derived(
		group.cards.some((c) => c.card_id === selectedCardId) ||
		(!!selectedEntityId && group.entity_id === selectedEntityId)
	);
	const isGroupLastViewed = $derived(
		!expanded && !isGroupSelected && !hasSelection && (
			(!!lastViewedCardId && group.cards.some(c => c.card_id === lastViewedCardId)) ||
			(!!lastViewedEntityId && group.entity_id === lastViewedEntityId)
		)
	);
	const isDimmed = $derived(hasSelection && !isGroupSelected);

	// Auto-expand when a card in this group is targeted for scroll
	$effect(() => {
		if (scrollToCardId && !expanded && group.cards.some((c) => c.card_id === scrollToCardId)) {
			expanded = true;
		}
	});


	// Close group menu on outside click — uses element ref so clicking
	// another group's menu correctly closes this one.
	$effect(() => {
		if (!groupMenuOpen) return;
		function handleClick(e: MouseEvent) {
			const target = e.target as HTMLElement;
			if (!menuEl?.contains(target)) groupMenuOpen = false;
		}
		document.addEventListener('click', handleClick, true);
		return () => document.removeEventListener('click', handleClick, true);
	});

	const platformLabel: Record<string, string> = {
		jira: 'Jira', gmail: 'Gmail', slack: 'Slack',
		bitbucket: 'Bitbucket', calendar: 'Calendar', github: 'GitHub', laya: 'Laya'
	};

	const sourceLabel = $derived(platformLabel[group.platform] ?? group.platform);

	const topCard = $derived(group.cards[0]);

	// Status priority for group color: highest-priority status wins
	const statusPriority: string[] = [
		'pending', 'awaiting_input', 'agent_running', 'failed',
		'ready', 'done', 'dismissed', 'archived'
	];

	const solidGroupRowStyle: Record<string, string> = {
		pending:            'bg-amber-950/55 card-pulse-amber',
		ready:              'bg-amber-950/55',
		agent_running:      'bg-violet-950/55 card-pulse-violet',
		awaiting_input:     'bg-violet-950/55',
		done:               'bg-emerald-950/50',
		failed:             'bg-rose-950/60',
		dismissed:          'bg-surface-800/40',
		archived:           'bg-surface-900/60',
	};
	const glassGroupRowStyle: Record<string, string> = {
		pending:            'glass-card-flat bg-amber-950/45 card-pulse-amber',
		ready:              'glass-card-flat bg-amber-950/45',
		agent_running:      'glass-card-flat bg-violet-950/45 card-pulse-violet',
		awaiting_input:     'glass-card-flat bg-violet-950/45',
		done:               'glass-card-flat bg-emerald-950/40',
		failed:             'glass-card-flat bg-rose-950/50',
		dismissed:          'glass-card-flat bg-surface-800/30',
		archived:           'glass-card-flat bg-surface-900/35',
	};
	const groupRowStyle = $derived($glassTheme ? glassGroupRowStyle : solidGroupRowStyle);

	const dominantStatus = $derived.by(() => {
		const statuses = new Set(group.cards.map(c => c.status));
		for (const s of statusPriority) {
			if (statuses.has(s as ActionCard['status'])) return s;
		}
		return group.cards[0]?.status ?? 'pending';
	});

	const allArchived = $derived(group.cards.every(c => c.status === 'archived'));

	const groupBgStyle = $derived(
		allArchived
			? ($glassTheme ? 'glass-card-flat bg-surface-900/30 opacity-50 hover:opacity-80' : 'bg-surface-900/60 opacity-50 hover:opacity-80')
			: $cardColors
				? (groupRowStyle[dominantStatus] ?? '')
				: ''
	);

	const statusDisplayLabel: Record<string, string> = {
		pending: 'processing', ready: 'ready',
		agent_running: 'running', awaiting_input: 'needs input', done: 'done',
		failed: 'failed', dismissed: 'dismissed', archived: 'archived'
	};

	const statusSummaryTooltip = $derived.by(() => {
		const counts = new Map<string, number>();
		for (const c of group.cards) {
			counts.set(c.status, (counts.get(c.status) ?? 0) + 1);
		}
		return [...counts.entries()]
			.map(([status, count]) => `${count} ${statusDisplayLabel[status] ?? status}`)
			.join(', ');
	});

	function timeAgo(dateStr?: string): string {
		if (!dateStr) return '';
		const utcStr = dateStr.endsWith('Z') || dateStr.includes('+') ? dateStr : dateStr + 'Z';
		const diff = Date.now() - new Date(utcStr).getTime();
		const mins = Math.floor(diff / 60000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hours = Math.floor(mins / 60);
		if (hours < 24) return `${hours}h ago`;
		return `${Math.floor(hours / 24)}d ago`;
	}

	// Group bulk selection state
	const groupCardIds = $derived(group.cards.map(c => c.card_id));
	const selectedInGroupCount = $derived(bulkSelectedIds ? groupCardIds.filter(id => bulkSelectedIds.has(id)).length : 0);
	const allGroupSelected = $derived(selectedInGroupCount === groupCardIds.length && groupCardIds.length > 0);
	const someGroupSelected = $derived(selectedInGroupCount > 0 && !allGroupSelected);

	let subjectEl: HTMLSpanElement | undefined = $state();
	let groupCheckboxEl: HTMLButtonElement | undefined = $state();
	let fixedTooltip = $state<{ text: string; top: number; left: number; maxWidth?: number } | null>(null);

	function showTooltip(el: HTMLElement, text: string, opts?: { maxWidth?: number }) {
		const rect = el.getBoundingClientRect();
		fixedTooltip = { text, top: rect.bottom + 4, left: rect.left, maxWidth: opts?.maxWidth };
	}
	function showTooltipIfTruncated(el: HTMLElement | undefined, text: string, opts?: { maxWidth?: number }) {
		if (!el) return;
		if (el.scrollWidth <= el.clientWidth) { fixedTooltip = null; return; }
		showTooltip(el, text, opts);
	}
	function hideTooltip() { fixedTooltip = null; }

	function toggleGroupCheckbox(e: MouseEvent) {
		e.stopPropagation();
		if (allGroupSelected) {
			onbulktogglegroup?.(groupCardIds, false);
		} else {
			onbulktogglegroup?.(groupCardIds, true);
		}
	}

	function toggle() { expanded = !expanded; }
	let menuPos = $state({ top: 0, right: 0 });
	function toggleGroupMenu(e: Event) {
		e.stopPropagation();
		if (!groupMenuOpen && menuEl) {
			const rect = menuEl.getBoundingClientRect();
			menuPos = { top: rect.bottom + 4, right: window.innerWidth - rect.right };
		}
		groupMenuOpen = !groupMenuOpen;
	}

	// Bulk actions
	const canCompleteAll = $derived(group.cards.some((c) => c.status !== 'done' && !['dismissed', 'archived', 'failed'].includes(c.status)));
	const canDismissAll = $derived(group.cards.some((c) => c.status !== 'dismissed' && !['archived', 'done', 'failed'].includes(c.status)));
	const canArchiveAll = $derived(group.cards.some((c) => c.status !== 'archived'));
	const canReopenAll = $derived(group.cards.some((c) => ['dismissed', 'archived', 'done'].includes(c.status)));
	const canUnarchiveAll = $derived(group.cards.some((c) => c.status === 'archived'));
	const hasAnyAction = $derived(canCompleteAll || canDismissAll || canArchiveAll || canReopenAll || canUnarchiveAll || !!onlink);

	async function bulkAction(action: 'complete' | 'dismiss' | 'archive' | 'reopen' | 'unarchive', e: Event) {
		e.stopPropagation();
		groupMenuOpen = false;
		bulkActionRunning = true;
		try {
			const promises: Promise<unknown>[] = [];
			for (const card of group.cards) {
				switch (action) {
					case 'complete':
						if (card.status !== 'done' && !['dismissed', 'archived', 'failed'].includes(card.status)) promises.push(engineApi.markCardDone(card.card_id).then(() => { card.status = 'done'; if (!card.read_at) card.read_at = new Date().toISOString(); }));
						break;
					case 'dismiss':
						if (card.status !== 'dismissed' && !['archived', 'done', 'failed'].includes(card.status)) promises.push(engineApi.dismissCard(card.card_id).then(() => { card.status = 'dismissed'; if (!card.read_at) card.read_at = new Date().toISOString(); }));
						break;
					case 'archive':
						if (card.status !== 'archived') promises.push(engineApi.archiveCard(card.card_id).then(() => { card.status = 'archived'; if (!card.read_at) card.read_at = new Date().toISOString(); }));
						break;
					case 'reopen':
						if (['dismissed', 'archived', 'done'].includes(card.status)) promises.push(engineApi.reopenCard(card.card_id).then(() => { card.status = 'ready'; }));
						break;
					case 'unarchive':
						if (card.status === 'archived') promises.push(engineApi.reopenCard(card.card_id).then(() => { card.status = 'ready'; }));
						break;
				}
			}
			await Promise.all(promises);
		} finally {
			bulkActionRunning = false;
		}
	}
</script>

<div bind:this={wrapperEl} class="transition-opacity
		{isDimmed ? ($glassTheme ? 'glass-dim' : 'opacity-45 hover:opacity-70') : ''}" data-group-entity={group.entity_id}>
	<div class="flex items-center {onbulktoggle ? 'gap-1.5' : ''}">
		{#if onbulktoggle}
			<div class="w-5 shrink-0 flex items-center justify-center">
				<button
					bind:this={groupCheckboxEl}
					class="h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors
						{allGroupSelected
							? 'bg-laya-orange border-laya-orange'
							: someGroupSelected
								? 'bg-laya-orange/50 border-laya-orange'
								: 'border-surface-500 hover:border-surface-300 bg-transparent'}"
					onclick={toggleGroupCheckbox}
					aria-label="{allGroupSelected ? 'Deselect' : 'Select'} all cards in group"
				>
					{#if allGroupSelected}
						<svg class="h-2.5 w-2.5 text-white" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
						</svg>
					{:else if someGroupSelected}
						<svg class="h-2.5 w-2.5 text-white" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
							<path stroke-linecap="round" d="M6 12h12" />
						</svg>
					{/if}
				</button>
			</div>
		{/if}

		<div data-group-row={group.entity_id}
			data-status={$glassTheme && $cardColors && !allArchived && !expanded ? dominantStatus : undefined}
			class="relative flex flex-1 min-w-0 items-center rounded-lg border hover:z-20 transition-colors
				{expanded
					? 'border-transparent'
					: 'list-row-hover border-transparent ' + groupBgStyle}
				{isGroupLastViewed ? ($cardColors ? 'card-last-viewed card-last-viewed--compact' : 'card-last-viewed-highlight') : ''}">

		<!-- Group header row -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="group/grow flex flex-1 min-w-0 items-center px-3 py-1.5 cursor-pointer transition-colors"
				onclick={() => {
					if (expanded) {
						expanded = false;
					} else if (onselectgroup) {
						onselectgroup(group);
					} else {
						onselect(topCard);
					}
				}}
				onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (expanded) { expanded = false; } else if (onselectgroup) { onselectgroup(group); } else { onselect(topCard); } } }}
				role="button"
				tabindex="0"
			>
				<!-- Expand/collapse chevron — visual stays w-5 (matches ListRow bookmark column).
				     Click target overflows vertically into the row's py-1.5 padding and leftward
				     into the row's px-3 padding, but stops at the right edge so the hover bg
				     doesn't bleed into the source dot. -->
				<div class="group/chev relative w-5 shrink-0 flex items-center justify-center">
					<button
						aria-label="{expanded ? 'Collapse' : 'Expand'} group"
						class="absolute inset-0 -my-1.5 -ml-2 rounded"
						onclick={(e) => { e.stopPropagation(); toggle(); }}
					></button>
					<svg
						class="pointer-events-none relative h-3 w-3 transition-colors text-surface-500 group-hover/chev:text-laya-orange {expanded ? '' : '-rotate-90'}"
						fill="none" stroke="currentColor" viewBox="0 0 24 24"
					>
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
					</svg>
				</div>

				<!-- Source — fixed width, matches ListRow; brand dot prefix mirrors ListRow -->
		<span class="w-[76px] shrink-0 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-surface-500 truncate" title={sourceLabel}>
			<span class="h-1 w-1 rounded-full shrink-0" style="background-color: {platformDotColor(group.platform)}"></span>
			<span class="truncate">{sourceLabel}</span>
		</span>
		<!-- Spacer slot — mirrors ListRow's icon spacer so subjectId aligns with ListRow's actor column -->
		<span class="w-3 shrink-0 ml-2"></span>
		<span class="w-[90px] shrink-0 ml-1 text-[11px] font-medium text-laya-orange/70 truncate">
			{subjectId ?? ''}
		</span>

		<!-- Subject (entity title) -->
		<span class="min-w-0 flex-1 ml-2 flex items-center gap-1"
			onmouseenter={() => showTooltipIfTruncated(subjectEl, group.entity_title, { maxWidth: 320 })}
			onmouseleave={hideTooltip}
		>
			<span bind:this={subjectEl} class="min-w-0 block truncate text-xs {group.unread_count > 0 ? 'font-semibold text-surface-100' : 'font-normal text-surface-300'}">
				{group.entity_title}
			</span>
			{#if hasBookmark}
				<svg class="h-3 w-3 shrink-0 text-laya-orange/60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" stroke-dasharray="3 2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
				</svg>
			{/if}
		</span>

		<!-- Card count — aligned with ListRow status column (w-[70px]) -->
		<div class="w-[70px] shrink-0 flex items-center ml-2">
			<button
				class="rounded-full bg-laya-orange/10 px-2 py-0.5 text-[10px] font-semibold text-laya-orange hover:bg-laya-orange/20 transition-colors whitespace-nowrap"
				title="Show all cards"
				onclick={(e) => { e.stopPropagation(); expanded = !expanded; }}
				onmouseenter={(e) => showTooltip(e.currentTarget, statusSummaryTooltip)}
				onmouseleave={hideTooltip}
			>
				{group.card_count} cards{#if group.unread_count > 0} · {group.unread_count} new{/if}
			</button>
		</div>

		<!-- Three-dot menu — aligned with ListRow action buttons column (w-[68px]) -->
		<div class="w-[68px] shrink-0 flex items-center justify-end">
			{#if hasAnyAction}
				<div class="group-menu relative" bind:this={menuEl}>
					<button
						class="flex h-5 w-5 items-center justify-center rounded text-surface-500 hover:bg-surface-700 hover:text-surface-300 disabled:opacity-50 opacity-0 group-hover/grow:opacity-100 transition-opacity"
						onclick={toggleGroupMenu}
						disabled={bulkActionRunning}
						title="Group actions"
					>
						{#if bulkActionRunning}
							<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
								<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
								<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
							</svg>
						{:else}
							<svg class="h-3 w-3" fill="currentColor" viewBox="0 0 20 20">
								<path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zM12 10a2 2 0 11-4 0 2 2 0 014 0zM18 10a2 2 0 11-4 0 2 2 0 014 0z" />
							</svg>
						{/if}
					</button>
				</div>
			{/if}
		</div>

		<!-- Persona spacer — matches ListRow w-[62px] -->
		<span class="w-[62px] shrink-0 ml-1"></span>

		<!-- Priority spacer — matches ListRow w-[36px] -->
		<span class="w-[36px] shrink-0 ml-1"></span>

		<!-- Space badge — fixed width, matches ListRow -->
		<span class="w-[72px] shrink-0 flex items-center gap-1 ml-1 truncate">
			{#if topCard?.space_name}
				<span class="inline-flex items-center gap-1 rounded border border-surface-700 bg-surface-800/60 px-1.5 py-0.5 text-[9px] text-surface-400 truncate">
					<span class="h-1.5 w-1.5 rounded-full shrink-0" style="background-color: {topCard.space_color ?? '#F97316'}"></span>
					<span class="truncate">{topCard.space_name}</span>
				</span>
			{/if}
		</span>

		<!-- Time — fixed width, matches ListRow -->
		<span class="w-[52px] shrink-0 text-right text-[10px] text-surface-500 whitespace-nowrap">{timeAgo(group.latest_at)}</span>
	</div>
	</div>
	</div>

	{#if expanded}
		<div transition:slide={{ duration: $reducedMotion ? 0 : 200 }} class="flex flex-col gap-0.5 pt-1">
			{#each group.cards as card (card.card_id)}
				<ListRow {card} {onselect} {ondelete} {selectedCardId} indented={true}
					{hasSelection} {lastViewedCardId}
					bulkSelected={bulkSelectedIds?.has(card.card_id) ?? false}
					{onbulktoggle} />
			{/each}
		</div>
	{/if}
</div>

{#if groupMenuOpen}
	<div
		use:portal
		class="fixed z-[100] w-40 rounded-lg border p-1 {$glassTheme ? 'glass-menu' : 'border-surface-600 bg-surface-800 shadow-xl shadow-black/30'}"
		style="top: {menuPos.top}px; right: {menuPos.right}px;"
		role="menu"
		tabindex="-1"
		onclick={(e) => e.stopPropagation()}
		onkeydown={(e) => { if (e.key === 'Escape') groupMenuOpen = false; }}
	>
		{#if canCompleteAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-green-400" role="menuitem" onclick={(e) => bulkAction('complete', e)}>Complete All</button>
		{/if}
		{#if canDismissAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-red-400" role="menuitem" onclick={(e) => bulkAction('dismiss', e)}>Dismiss All</button>
		{/if}
		{#if canReopenAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-laya-orange" role="menuitem" onclick={(e) => bulkAction('reopen', e)}>Reopen All</button>
		{/if}
		{#if canArchiveAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-surface-400" role="menuitem" onclick={(e) => bulkAction('archive', e)}>Archive All</button>
		{/if}
		{#if canUnarchiveAll}
			<button class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-laya-orange" role="menuitem" onclick={(e) => bulkAction('unarchive', e)}>Unarchive All</button>
		{/if}
		<div class="my-1 border-t border-surface-700"></div>
		<button
			class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-surface-300 hover:bg-surface-700 hover:text-laya-orange"
			role="menuitem"
			onclick={(e) => { e.stopPropagation(); groupMenuOpen = false; onlink?.(group); }}
		>
			<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>
			Link to...
		</button>
	</div>
{/if}

{#if fixedTooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] rounded-md border border-transparent glass-tooltip px-2 py-1 text-[10px] font-medium"
		style="top: {fixedTooltip.top}px; left: {fixedTooltip.left}px;{fixedTooltip.maxWidth ? ` max-width: ${fixedTooltip.maxWidth}px; white-space: normal;` : ' white-space: nowrap;'}"
	>
		{fixedTooltip.text}
	</span>
{/if}
