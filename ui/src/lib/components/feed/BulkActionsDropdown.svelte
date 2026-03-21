<script lang="ts">
	import type { ActionCard } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';
	import { feedSelection } from '$lib/stores/feedSelection';

	let {
		selectedCards,
		ondelete
	}: {
		selectedCards: ActionCard[];
		ondelete?: (cardId: string) => void;
	} = $props();

	let open = $state(false);
	let running = $state(false);
	let confirm = $state<{ key: string; label: string; applicable: ActionCard[]; total: number; isDelete: boolean } | null>(null);

	// Close dropdown on outside click
	$effect(() => {
		if (!open) return;
		function handleClick(e: MouseEvent) {
			const target = e.target as HTMLElement;
			if (!target.closest('.bulk-actions-menu')) open = false;
		}
		document.addEventListener('click', handleClick, true);
		return () => document.removeEventListener('click', handleClick, true);
	});

	const actions: {
		key: string;
		label: string;
		icon: string;
		color: string;
		filter: (c: ActionCard) => boolean;
	}[] = [
		{
			key: 'done', label: 'Mark Done',
			icon: 'M5 13l4 4L19 7',
			color: 'text-green-400',
			filter: (c) => c.status !== 'done' && !['dismissed', 'archived', 'failed'].includes(c.status)
		},
		{
			key: 'approve', label: 'Approve Agent',
			icon: 'M8 5v14l11-7z',
			color: 'text-violet-400',
			filter: (c) => c.status === 'requires_approval'
		},
		{
			key: 'dismiss', label: 'Dismiss',
			icon: 'M6 18L18 6M6 6l12 12',
			color: 'text-surface-300',
			filter: (c) => c.status !== 'dismissed' && !['archived', 'done', 'failed'].includes(c.status)
		},
		{
			key: 'reopen', label: 'Reopen',
			icon: 'M3 10h10a5 5 0 010 10H9m-6-10l4-4m-4 4l4 4',
			color: 'text-laya-orange',
			filter: (c) => ['dismissed', 'archived', 'done'].includes(c.status)
		},
		{
			key: 'archive', label: 'Archive',
			icon: 'M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4',
			color: 'text-surface-400',
			filter: (c) => c.status !== 'archived'
		},
		{
			key: 'unarchive', label: 'Unarchive',
			icon: 'M3 10h10a5 5 0 010 10H9m-6-10l4-4m-4 4l4 4',
			color: 'text-laya-orange',
			filter: (c) => c.status === 'archived'
		},
		{
			key: 'delete', label: 'Delete',
			icon: 'M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16',
			color: 'text-red-400',
			filter: () => true
		}
	];

	const actionStats = $derived(
		actions.map((a) => {
			const applicable = selectedCards.filter(a.filter);
			return { ...a, applicable, applicableCount: applicable.length, total: selectedCards.length };
		})
	);

	function handleActionClick(stat: typeof actionStats[number]) {
		if (stat.applicableCount === 0) return;

		const isDelete = stat.key === 'delete';
		const isPartial = stat.applicableCount < stat.total;

		if (isDelete || isPartial) {
			confirm = {
				key: stat.key,
				label: stat.label,
				applicable: stat.applicable,
				total: stat.total,
				isDelete
			};
			open = false;
		} else {
			executeAction(stat.key, stat.applicable);
		}
	}

	async function executeAction(actionKey: string, cards: ActionCard[]) {
		running = true;
		open = false;
		confirm = null;
		try {
			const promises: Promise<unknown>[] = [];
			for (const card of cards) {
				switch (actionKey) {
					case 'done':
						promises.push(engineApi.markCardDone(card.card_id).then(() => { card.status = 'done'; }));
						break;
					case 'approve':
						promises.push(engineApi.approveAgent(card.card_id).then(() => { card.status = 'agent_running'; }));
						break;
					case 'dismiss':
						promises.push(engineApi.dismissCard(card.card_id).then(() => { card.status = 'dismissed'; }));
						break;
					case 'archive':
						promises.push(engineApi.archiveCard(card.card_id).then(() => { card.status = 'archived'; }));
						break;
					case 'reopen':
					case 'unarchive':
						promises.push(engineApi.reopenCard(card.card_id).then(() => { card.status = 'ready'; }));
						break;
					case 'delete':
						promises.push(engineApi.deleteCard(card.card_id).then(() => {
							feedSelection.removeDeleted(card.card_id);
							ondelete?.(card.card_id);
						}));
						break;
				}
			}
			await Promise.all(promises);
			feedSelection.deselectAll();
		} finally {
			running = false;
		}
	}
</script>

<div class="bulk-actions-menu relative">
	<!-- Trigger button -->
	<button
		onclick={(e) => { e.stopPropagation(); open = !open; }}
		disabled={running}
		class="flex items-center gap-1.5 rounded-lg border border-laya-orange/30 bg-laya-orange/10 px-3 py-1 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/20 disabled:opacity-50"
	>
		{#if running}
			<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
				<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
				<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
			</svg>
			Applying...
		{:else}
			<svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
			</svg>
			Actions
		{/if}
	</button>

	<!-- Dropdown -->
	{#if open}
		<div class="absolute left-0 top-full z-50 mt-1 w-56 rounded-lg border border-surface-600 bg-surface-800 p-1 shadow-xl shadow-black/30">
			{#each actionStats as stat, i}
				{#if i === actionStats.length - 1}
					<div class="my-1 border-t border-surface-700"></div>
				{/if}
				<button
					class="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs transition-colors
						{stat.applicableCount === 0
							? 'text-surface-600 cursor-not-allowed'
							: `text-surface-300 hover:bg-surface-700 hover:${stat.color}`}"
					onclick={() => handleActionClick(stat)}
					disabled={stat.applicableCount === 0}
				>
					<svg class="h-3 w-3 shrink-0 {stat.applicableCount > 0 ? stat.color : 'text-surface-600'}" fill={stat.key === 'approve' ? 'currentColor' : 'none'} stroke={stat.key === 'approve' ? 'none' : 'currentColor'} stroke-width="2" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" d={stat.icon} />
					</svg>
					<span class="flex-1 text-left">{stat.label}</span>
					<span class="text-[10px] {stat.applicableCount === 0 ? 'text-surface-600' : 'text-surface-500'}">
						{stat.applicableCount} of {stat.total}
					</span>
				</button>
			{/each}
		</div>
	{/if}
</div>

<!-- Confirmation dialog -->
{#if confirm}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
		role="dialog"
		aria-label="Confirm bulk action"
		tabindex="-1"
		onclick={(e) => { if (e.target === e.currentTarget) confirm = null; }}
		onkeydown={(e) => { if (e.key === 'Escape') confirm = null; }}
	>
		<div class="mx-4 w-full max-w-sm rounded-xl border {confirm.isDelete ? 'border-red-800/40' : 'border-laya-orange/30'} bg-surface-800 p-5 shadow-2xl">
			<div class="mb-3 flex items-start gap-3">
				<div class="mt-0.5 rounded-full {confirm.isDelete ? 'bg-red-950/60' : 'bg-laya-orange/10'} p-1.5">
					{#if confirm.isDelete}
						<svg class="h-4 w-4 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
						</svg>
					{:else}
						<svg class="h-4 w-4 text-laya-orange" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M12 2a10 10 0 100 20 10 10 0 000-20z" />
						</svg>
					{/if}
				</div>
				<div>
					<h4 class="text-sm font-semibold text-surface-50">{confirm.label}</h4>
					{#if confirm.isDelete}
						<p class="mt-1 text-xs leading-relaxed text-surface-400">
							This will permanently delete {confirm.applicable.length} card{confirm.applicable.length !== 1 ? 's' : ''}. This action cannot be undone.
						</p>
					{:else if confirm.applicable.length < confirm.total}
						<p class="mt-1 text-xs leading-relaxed text-surface-400">
							This action will apply to <span class="font-medium text-laya-orange">{confirm.applicable.length}</span> of {confirm.total} selected cards. Cards where this action is not applicable will be skipped.
						</p>
					{/if}
				</div>
			</div>
			<div class="flex justify-end gap-2">
				<button
					class="rounded-md px-3 py-1.5 text-xs text-surface-400 hover:text-surface-200"
					onclick={() => confirm = null}
				>
					Cancel
				</button>
				<button
					class="rounded-md px-3 py-1.5 text-xs font-medium {confirm.isDelete ? 'bg-red-700 text-red-50 hover:bg-red-600' : 'bg-laya-orange/20 text-laya-orange hover:bg-laya-orange/30'}"
					onclick={() => { if (confirm) executeAction(confirm.key, confirm.applicable); }}
				>
					{confirm.isDelete ? 'Delete' : 'Confirm'}
				</button>
			</div>
		</div>
	</div>
{/if}
