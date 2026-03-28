<script lang="ts">
	import type { DaySummary, SummaryItem } from '$lib/api/types';

	let {
		summary,
		updatedAt,
		ongotocard,
		spaceFilter = []
	}: {
		summary: DaySummary | null;
		updatedAt: string | null;
		ongotocard: (cardId: string) => void;
		spaceFilter?: string[];
	} = $props();

	function priorityColor(priority: string): string {
		switch (priority) {
			case 'CRITICAL': return 'text-laya-coral';
			case 'HIGH': return 'text-laya-orange';
			case 'MEDIUM': return 'text-laya-gold';
			case 'LOW': return 'text-laya-amber';
			default: return 'text-surface-400';
		}
	}

	function statusIcon(status: string): string {
		switch (status) {
			case 'done': return '✓';
			case 'dismissed': return '✗';
			case 'archived': return '▪';
			default: return '○';
		}
	}

	function statusClass(status: string): string {
		switch (status) {
			case 'done': return 'text-laya-gold line-through opacity-70';
			case 'dismissed': return 'text-surface-500 line-through opacity-50';
			case 'archived': return 'text-surface-500 line-through opacity-50';
			default: return 'text-surface-200';
		}
	}

	function formatTime(iso: string): string {
		return new Date(iso).toLocaleTimeString(undefined, {
			hour: '2-digit',
			minute: '2-digit'
		});
	}

	function matchesSpace(item: SummaryItem): boolean {
		if (!spaceFilter.length) return true;
		return spaceFilter.includes(item.space_id || 'default');
	}

	/** Collect unique spaces across all summary items for the legend */
	const uniqueSpaces = $derived.by(() => {
		if (!summary) return [];
		const all = [
			...summary.events_and_meetings,
			...summary.action_items,
			...summary.key_updates,
		];
		const seen = new Map<string, { name: string; color: string }>();
		for (const item of all) {
			const sid = item.space_id || 'default';
			if (!seen.has(sid)) {
				seen.set(sid, {
					name: item.space_name || 'Default',
					color: item.space_color || '#F97316',
				});
			}
		}
		return [...seen.entries()].map(([id, info]) => ({ id, ...info }));
	});

	const hasMultipleSpaces = $derived(uniqueSpaces.length > 1);

	const hasContent = $derived(
		summary &&
		(summary.events_and_meetings.length > 0 ||
		 summary.action_items.length > 0 ||
		 summary.key_updates.length > 0)
	);

	// Filtered lists
	const filteredEvents = $derived(
		summary ? summary.events_and_meetings.filter(matchesSpace) : []
	);
	const filteredActions = $derived(
		summary ? summary.action_items.filter(matchesSpace) : []
	);
	const filteredUpdates = $derived(
		summary ? summary.key_updates.filter(matchesSpace) : []
	);

	// Filtered counts for "(n filtered)" badges
	const eventsFilteredCount = $derived(
		summary ? summary.events_and_meetings.length - filteredEvents.length : 0
	);
	const actionsFilteredCount = $derived(
		summary ? summary.action_items.length - filteredActions.length : 0
	);
	const updatesFilteredCount = $derived(
		summary ? summary.key_updates.length - filteredUpdates.length : 0
	);

	const pendingActions = $derived(
		filteredActions.filter(i => i.status === 'pending').length
	);
	const totalActions = $derived(filteredActions.length);
</script>

{#if !summary || !hasContent}
	<div class="flex h-full flex-col items-center justify-center text-surface-500">
		<svg class="mb-3 h-10 w-10 text-laya-orange opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
		</svg>
		<p class="text-sm">No summary yet</p>
		<p class="mt-1 text-xs text-surface-600">Summary builds as events arrive throughout the day</p>
	</div>
{:else}
	<div class="flex flex-col gap-5">
		<!-- Last updated -->
		{#if updatedAt}
			<p class="text-[10px] text-surface-500">Last updated {formatTime(updatedAt)}</p>
		{/if}

		<!-- Space legend (only when multiple spaces) -->
		{#if hasMultipleSpaces}
			<div class="flex flex-wrap gap-2">
				{#each uniqueSpaces as space}
					<span class="summary-space-legend" style:--space-color={space.color}>
						<span class="summary-space-legend-dot" style:background={space.color}></span>
						{space.name}
					</span>
				{/each}
			</div>
		{/if}

		<!-- Events & Meetings -->
		{#if summary.events_and_meetings.length > 0}
			<section class="summary-section summary-section--events">
				<div class="summary-section-header">
					<div class="summary-section-icon summary-section-icon--events">
						<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
						</svg>
					</div>
					<h3 class="summary-section-title">Events & Meetings</h3>
					<span class="summary-section-badge summary-section-badge--events">{filteredEvents.length}</span>
					{#if eventsFilteredCount > 0}
						<span class="text-[10px] text-surface-500">({eventsFilteredCount} filtered)</span>
					{/if}
				</div>
				{#if filteredEvents.length > 0}
					<div class="flex flex-col gap-0.5">
						{#each filteredEvents as item}
							<button
								class="summary-item group"
								class:summary-item--spaced={!!item.space_name}
								style:--space-color={item.space_color || '#F97316'}
								onclick={() => ongotocard(item.card_id)}
							>
								<span class="summary-item-status {statusClass(item.status)}">{statusIcon(item.status)}</span>
								<span class="flex-1 text-[13px] leading-snug {statusClass(item.status)}">{item.text}</span>
								{#if item.space_name}
									<span class="summary-item-space" style:--space-color={item.space_color || '#F97316'}>{item.space_name}</span>
								{/if}
								<span class="summary-item-priority {priorityColor(item.priority)}">{item.priority}</span>
							</button>
						{/each}
					</div>
				{:else}
					<p class="text-xs text-surface-500 px-1">No items match the selected space</p>
				{/if}
			</section>
		{/if}

		<!-- Action Items -->
		{#if summary.action_items.length > 0}
			<section class="summary-section summary-section--actions">
				<div class="summary-section-header">
					<div class="summary-section-icon summary-section-icon--actions">
						<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
						</svg>
					</div>
					<h3 class="summary-section-title">Action Items</h3>
					<span class="summary-section-badge summary-section-badge--actions">
						{pendingActions}/{totalActions}
					</span>
					{#if actionsFilteredCount > 0}
						<span class="text-[10px] text-surface-500">({actionsFilteredCount} filtered)</span>
					{/if}
					{#if pendingActions > 0}
						<span class="ml-auto text-[10px] text-laya-orange">{pendingActions} pending</span>
					{/if}
				</div>
				{#if filteredActions.length > 0}
					<!-- Progress bar -->
					<div class="summary-progress-track mx-1 mb-2 h-1 overflow-hidden rounded-full">
						<div
							class="h-full rounded-full bg-laya-orange/60 transition-all duration-500"
							style="width: {totalActions > 0 ? ((totalActions - pendingActions) / totalActions) * 100 : 0}%"
						></div>
					</div>
					<div class="flex flex-col gap-0.5">
						{#each filteredActions as item}
							<button
								class="summary-item group"
								class:summary-item--spaced={!!item.space_name}
								style:--space-color={item.space_color || '#F97316'}
								onclick={() => ongotocard(item.card_id)}
							>
								<span class="summary-item-status {statusClass(item.status)}">{statusIcon(item.status)}</span>
								<span class="flex-1 text-[13px] leading-snug {statusClass(item.status)}">{item.text}</span>
								{#if item.space_name}
									<span class="summary-item-space" style:--space-color={item.space_color || '#F97316'}>{item.space_name}</span>
								{/if}
								<span class="summary-item-priority {priorityColor(item.priority)}">{item.priority}</span>
							</button>
						{/each}
					</div>
				{:else}
					<p class="text-xs text-surface-500 px-1">No items match the selected space</p>
				{/if}
			</section>
		{/if}

		<!-- Key Updates -->
		{#if summary.key_updates.length > 0}
			<section class="summary-section summary-section--updates">
				<div class="summary-section-header">
					<div class="summary-section-icon summary-section-icon--updates">
						<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
						</svg>
					</div>
					<h3 class="summary-section-title">Key Updates</h3>
					<span class="summary-section-badge summary-section-badge--updates">{filteredUpdates.length}</span>
					{#if updatesFilteredCount > 0}
						<span class="text-[10px] text-surface-500">({updatesFilteredCount} filtered)</span>
					{/if}
				</div>
				{#if filteredUpdates.length > 0}
					<div class="flex flex-col gap-0.5">
						{#each filteredUpdates as item}
							<button
								class="summary-item group"
								class:summary-item--spaced={!!item.space_name}
								style:--space-color={item.space_color || '#F97316'}
								onclick={() => ongotocard(item.card_id)}
							>
								<span class="summary-item-status {statusClass(item.status)}">{statusIcon(item.status)}</span>
								<span class="flex-1 text-[13px] leading-snug {statusClass(item.status)}">{item.text}</span>
								{#if item.space_name}
									<span class="summary-item-space" style:--space-color={item.space_color || '#F97316'}>{item.space_name}</span>
								{/if}
								<span class="summary-item-priority {priorityColor(item.priority)}">{item.priority}</span>
							</button>
						{/each}
					</div>
				{:else}
					<p class="text-xs text-surface-500 px-1">No items match the selected space</p>
				{/if}
			</section>
		{/if}
	</div>
{/if}

<style>
	/* ── Section containers ── */
	.summary-section {
		border-radius: 0.75rem;
		padding: 0.875rem 1rem;
	}
	.summary-section--events {
		background: color-mix(in oklch, var(--color-laya-peach) 6%, transparent);
	}
	.summary-section--actions {
		background: color-mix(in oklch, var(--color-laya-orange) 6%, transparent);
	}
	.summary-section--updates {
		background: color-mix(in oklch, var(--color-laya-gold) 6%, transparent);
	}

	/* Light theme: use darker, more saturated tints so sections
	   are clearly visible against the cream background (surface-900 ≈ 0.97).
	   The raw brand vars are too light (peach 0.905, gold 0.810) to tint cream. */
	:global([data-theme='light']) .summary-section--events {
		background: oklch(0.94 0.035 65 / 70%);
	}
	:global([data-theme='light']) .summary-section--actions {
		background: oklch(0.93 0.045 58 / 65%);
	}
	:global([data-theme='light']) .summary-section--updates {
		background: oklch(0.94 0.040 75 / 65%);
	}

	/* ── Section header ── */
	.summary-section-header {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-bottom: 0.625rem;
	}

	/* ── Icon circle ── */
	.summary-section-icon {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 1.75rem;
		height: 1.75rem;
		border-radius: 0.5rem;
		flex-shrink: 0;
	}
	.summary-section-icon--events {
		background: color-mix(in oklch, var(--color-laya-peach) 18%, transparent);
		color: var(--color-laya-peach);
	}
	.summary-section-icon--actions {
		background: color-mix(in oklch, var(--color-laya-orange) 18%, transparent);
		color: var(--color-laya-orange);
	}
	.summary-section-icon--updates {
		background: color-mix(in oklch, var(--color-laya-gold) 18%, transparent);
		color: var(--color-laya-gold);
	}
	:global([data-theme='light']) .summary-section-icon--events {
		background: oklch(0.88 0.06 65 / 60%);
		color: oklch(0.50 0.10 60);
	}
	:global([data-theme='light']) .summary-section-icon--actions {
		background: oklch(0.86 0.07 55 / 55%);
		color: oklch(0.48 0.14 55);
	}
	:global([data-theme='light']) .summary-section-icon--updates {
		background: oklch(0.88 0.06 72 / 55%);
		color: oklch(0.48 0.12 72);
	}

	/* ── Section title ── */
	.summary-section-title {
		font-size: 0.7rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-surface-400);
	}

	/* ── Count badge ── */
	.summary-section-badge {
		font-size: 0.625rem;
		font-weight: 600;
		padding: 0.1rem 0.4rem;
		border-radius: 9999px;
	}
	.summary-section-badge--events {
		background: color-mix(in oklch, var(--color-laya-peach) 15%, transparent);
		color: var(--color-laya-peach);
	}
	.summary-section-badge--actions {
		background: color-mix(in oklch, var(--color-laya-orange) 15%, transparent);
		color: var(--color-laya-orange);
	}
	.summary-section-badge--updates {
		background: color-mix(in oklch, var(--color-laya-gold) 15%, transparent);
		color: var(--color-laya-gold);
	}
	:global([data-theme='light']) .summary-section-badge--events {
		background: oklch(0.88 0.06 65 / 50%);
		color: oklch(0.45 0.10 60);
	}
	:global([data-theme='light']) .summary-section-badge--actions {
		background: oklch(0.86 0.07 55 / 45%);
		color: oklch(0.44 0.14 55);
	}
	:global([data-theme='light']) .summary-section-badge--updates {
		background: oklch(0.88 0.06 72 / 45%);
		color: oklch(0.44 0.12 72);
	}

	/* ── Progress bar track ── */
	.summary-progress-track {
		background: var(--color-surface-800);
		opacity: 0.6;
	}
	:global([data-theme='light']) .summary-progress-track {
		background: oklch(0.78 0.025 60);
		opacity: 0.5;
	}

	/* ── Item row ── */
	.summary-item {
		display: flex;
		align-items: flex-start;
		gap: 0.625rem;
		padding: 0.4rem 0.5rem;
		padding-right: 3.5rem;
		border-radius: 0.5rem;
		text-align: left;
		transition: background-color 150ms;
		cursor: pointer;
		position: relative;
	}
	.summary-item:hover {
		background: var(--color-surface-800);
	}
	:global([data-theme='light']) .summary-item:hover {
		background: color-mix(in oklch, var(--color-surface-700) 40%, transparent);
	}
	.summary-item-status {
		margin-top: 0.15rem;
		font-size: 0.7rem;
		flex-shrink: 0;
		width: 0.875rem;
		text-align: center;
	}
	.summary-item-priority {
		position: absolute;
		right: 0.5rem;
		top: 0.45rem;
		font-size: 0.6rem;
		font-weight: 500;
		opacity: 0;
		transition: opacity 150ms;
	}
	.summary-item:hover .summary-item-priority {
		opacity: 1;
	}

	/* ── Space-aware background tint ── */
	.summary-item--spaced {
		background: color-mix(in oklch, var(--space-color, #F97316) 6%, transparent);
	}
	.summary-item--spaced:hover {
		background: color-mix(in oklch, var(--space-color, #F97316) 12%, transparent);
	}
	:global([data-theme='light']) .summary-item--spaced {
		background: color-mix(in oklch, var(--space-color, #F97316) 10%, transparent);
	}
	:global([data-theme='light']) .summary-item--spaced:hover {
		background: color-mix(in oklch, var(--space-color, #F97316) 18%, transparent);
	}

	/* ── Space badge (inline pill) ── */
	.summary-item-space {
		font-size: 0.55rem;
		font-weight: 500;
		padding: 0.05rem 0.35rem;
		border-radius: 9999px;
		background: color-mix(in oklch, var(--space-color, #F97316) 12%, transparent);
		color: var(--space-color, #F97316);
		white-space: nowrap;
		flex-shrink: 0;
		margin-top: 0.1rem;
		opacity: 0.7;
		transition: opacity 150ms;
	}
	.summary-item:hover .summary-item-space {
		opacity: 1;
	}
	:global([data-theme='light']) .summary-item-space {
		background: color-mix(in oklch, var(--space-color, #F97316) 18%, transparent);
	}

	/* ── Space legend (top bar) ── */
	.summary-space-legend {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		font-size: 0.6rem;
		font-weight: 500;
		color: var(--color-surface-400);
	}
	.summary-space-legend-dot {
		width: 0.4rem;
		height: 0.4rem;
		border-radius: 9999px;
		flex-shrink: 0;
	}
</style>
