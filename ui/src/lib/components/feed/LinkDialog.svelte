<script lang="ts">
	import type { CardGroup } from '$lib/api/types';
	import { engineApi } from '$lib/api/engine';

	let {
		sourceGroup,
		allGroups,
		onclose,
	}: {
		sourceGroup: CardGroup;
		allGroups: CardGroup[];
		onclose: () => void;
	} = $props();

	let searchQuery = $state('');
	let selectedTarget: CardGroup | null = $state(null);
	let linking = $state(false);
	let error = $state('');

	// Filter out the source group and match by search query
	const filteredGroups = $derived(
		allGroups
			.filter((g) => {
				// Exclude the source group itself
				if (g.entity_id === sourceGroup.entity_id && g.context_id === sourceGroup.context_id) return false;
				// Also exclude if it's literally the same group key
				const sourceKey = sourceGroup.context_id || sourceGroup.entity_id;
				const targetKey = g.context_id || g.entity_id;
				if (sourceKey === targetKey) return false;
				return true;
			})
			.filter((g) => {
				if (!searchQuery.trim()) return true;
				const q = searchQuery.toLowerCase();
				const title = (g.context_label ?? g.entity_title).toLowerCase();
				const topCardSummary = g.cards[0]?.summary?.toLowerCase() ?? '';
				return title.includes(q) || topCardSummary.includes(q);
			})
	);

	async function linkGroups() {
		if (!selectedTarget) return;
		linking = true;
		error = '';
		try {
			// Collect all card_ids from both groups
			const allCardIds = [
				...sourceGroup.cards.map((c) => c.card_id),
				...selectedTarget.cards.map((c) => c.card_id),
			];
			await engineApi.mergeCards(allCardIds);
			onclose();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Linking failed';
		} finally {
			linking = false;
		}
	}

	const platformLabel: Record<string, string> = {
		jira: 'Jira', gmail: 'Gmail', slack: 'Slack',
		bitbucket: 'Bitbucket', calendar: 'Calendar', github: 'GitHub', laya: 'Laya'
	};
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
	class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
	role="dialog"
	aria-label="Link groups"
	tabindex="-1"
	onclick={(e) => { if (e.target === e.currentTarget) onclose(); }}
	onkeydown={(e) => { if (e.key === 'Escape') onclose(); }}
>
	<div class="mx-4 w-full max-w-xl rounded-xl border border-surface-600 bg-surface-800 shadow-2xl">
		<!-- Header -->
		<div class="border-b border-surface-700 px-6 py-5">
			<h3 class="text-base font-semibold text-surface-100">Link to another group</h3>
			<p class="mt-1.5 text-sm text-surface-400">
				Select a group or card to link with
				<span class="font-medium text-surface-200">"{sourceGroup.context_label ?? sourceGroup.entity_title}"</span>
			</p>
		</div>

		<!-- Search -->
		<div class="border-b border-surface-700 px-6 py-3">
			<input
				type="text"
				bind:value={searchQuery}
				placeholder="Search groups..."
				class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder-surface-500 focus:border-laya-orange/50 focus:outline-none"
			/>
		</div>

		<!-- Group list -->
		<div class="max-h-96 overflow-y-auto px-3 py-2">
			{#if filteredGroups.length === 0}
				<p class="px-3 py-4 text-center text-xs text-surface-500">No matching groups found</p>
			{:else}
				{#each filteredGroups as group}
					<button
						class="flex w-full items-start gap-3 rounded-lg px-4 py-3 text-left transition-colors
							{selectedTarget?.entity_id === group.entity_id && selectedTarget?.context_id === group.context_id
								? 'bg-laya-orange/10 border border-laya-orange/30'
								: 'hover:bg-surface-700/60 border border-transparent'}"
						onclick={() => selectedTarget = group}
					>
						<div class="flex-1 min-w-0">
							<div class="flex items-center gap-1.5 mb-0.5">
								<span class="text-[10px] font-semibold uppercase tracking-widest text-surface-500">
									{platformLabel[group.platform] ?? group.platform}
								</span>
								{#if group.card_count > 1}
									<span class="rounded-full bg-surface-700 px-1.5 py-0.5 text-[10px] text-surface-400">
										{group.card_count} cards
									</span>
								{/if}
								{#if group.context_id}
									<span class="rounded-full bg-laya-orange/10 px-1.5 py-0.5 text-[9px] text-laya-orange/70">linked</span>
								{/if}
							</div>
							<p class="text-sm font-medium text-surface-200 truncate">
								{group.context_label ?? group.entity_title}
							</p>
							{#if group.cards[0]?.summary}
								<p class="mt-0.5 text-xs text-surface-500 line-clamp-2">{group.cards[0].summary}</p>
							{/if}
						</div>
						{#if selectedTarget?.entity_id === group.entity_id && selectedTarget?.context_id === group.context_id}
							<div class="mt-1 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-laya-orange text-[10px] text-white">
								<svg class="h-2.5 w-2.5" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg>
							</div>
						{/if}
					</button>
				{/each}
			{/if}
		</div>

		<!-- Footer -->
		<div class="border-t border-surface-700 px-6 py-4">
			{#if error}
				<p class="mb-2 text-xs text-red-400">{error}</p>
			{/if}
			<div class="flex justify-end gap-2">
				<button
					class="rounded-md px-3 py-1.5 text-xs text-surface-400 hover:text-surface-200"
					onclick={onclose}
				>
					Cancel
				</button>
				<button
					class="rounded-md px-3 py-1.5 text-xs font-medium bg-laya-orange/20 text-laya-orange hover:bg-laya-orange/30 disabled:opacity-40 disabled:cursor-not-allowed"
					disabled={!selectedTarget || linking}
					onclick={linkGroups}
				>
					{#if linking}
						Linking...
					{:else}
						Link Groups
					{/if}
				</button>
			</div>
		</div>
	</div>
</div>
