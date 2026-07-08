<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
	Filter popover, carved out of feed/+page.svelte (P7-7). It reads and mutates
	the shared feedFilters store directly (the parent's toolbar mirrors the same
	store), so the only props are open/positioning + whether any filter is active
	(to gate the "Clear all" row). Its root keeps the `filter-dropdown` class so the
	parent's class-based click-outside handler still recognises clicks inside it.
-->
<script lang="ts">
	import { feedFilters } from '$lib/stores/feedFilters';
	import { spaces } from '$lib/stores/spaces';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';

	let { open, pos, hasActiveFilters }: {
		open: boolean;
		pos: { top: number; left: number };
		hasActiveFilters: boolean;
	} = $props();

	// Toggle a value in a string[] filter array (add if absent, remove if present).
	function toggleFilter(arr: string[], value: string): string[] {
		return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value];
	}
</script>

{#if open}
	<div use:portal class="filter-dropdown fixed z-[100] w-64 overflow-y-auto rounded-xl border p-3 {$glassTheme ? 'glass-menu' : 'border-surface-600 bg-surface-800 shadow-xl shadow-black/30'}" style="top: {pos.top}px; left: {pos.left}px; max-height: calc(100vh - {pos.top}px - 16px);">
		<!-- Sort -->
		<div class="mb-3">
			<div class="mb-1.5 text-laya-micro font-semibold uppercase tracking-wider text-surface-500">Sort</div>
			<div class="flex items-center gap-1.5">
				<div class="flex flex-1 items-center gap-1.5 rounded-lg border border-surface-700 bg-surface-900/60 px-2 py-1">
					<svg class="h-3.5 w-3.5 text-surface-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4h13M3 8h9m-9 4h6m4 0l4-4m0 0l4 4m-4-4v12" />
					</svg>
					<select
						bind:value={$feedFilters.sortBy}
						class="flex-1 bg-transparent text-laya-secondary text-surface-200 outline-none cursor-pointer appearance-none pr-4"
						style="background-image: url('data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%2712%27 height=%2712%27 viewBox=%270 0 24 24%27 fill=%27none%27 stroke=%27%23888%27 stroke-width=%272%27%3E%3Cpath d=%27M6 9l6 6 6-6%27/%3E%3C/svg%3E'); background-repeat: no-repeat; background-position: right 0 center;"
					>
						<option value="newest">Newest</option>
						<option value="priority">Priority</option>
						<option value="status">Status</option>
						<option value="persona">Persona</option>
						<option value="category">Category</option>
						<option value="platform">Source</option>
						<option value="actor">Actor</option>
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
				<div class="mb-1.5 text-laya-micro font-semibold uppercase tracking-wider text-surface-500">Workspace</div>
				<div class="space-y-0.5">
					{#each $spaces as space}
						<button
							class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-laya-secondary transition-colors hover:bg-surface-700
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
			<div class="mb-1.5 text-laya-micro font-semibold uppercase tracking-wider text-surface-500">Status</div>
			<div class="space-y-0.5">
				{#each [['pending', 'Processing'], ['ready', 'Ready'], ['agent_running', 'Running'], ['failed', 'Failed'], ['done', 'Done'], ['dismissed', 'Dismissed']] as [value, label]}
					<button
						class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-laya-secondary transition-colors hover:bg-surface-700
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
			<div class="mb-1.5 text-laya-micro font-semibold uppercase tracking-wider text-surface-500">Priority</div>
			<div class="space-y-0.5">
				{#each [['CRITICAL', 'Critical'], ['HIGH', 'High'], ['MEDIUM', 'Medium'], ['LOW', 'Low']] as [value, label]}
					<button
						class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-laya-secondary transition-colors hover:bg-surface-700
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
				class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-laya-secondary transition-colors hover:bg-surface-700
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
			<button
				class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-laya-secondary transition-colors hover:bg-surface-700
					{$feedFilters.hasWorkspace ? 'text-laya-orange' : 'text-surface-300'}"
				onclick={() => ($feedFilters.hasWorkspace = !$feedFilters.hasWorkspace)}
			>
				<span class="flex h-4 w-4 items-center justify-center rounded border {$feedFilters.hasWorkspace ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-600'}">
					{#if $feedFilters.hasWorkspace}
						<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
						</svg>
					{/if}
				</span>
				Has Workspace
			</button>
			<button
				class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-laya-secondary transition-colors hover:bg-surface-700
					{$feedFilters.showUnreadOnly ? 'text-laya-orange' : 'text-surface-300'}"
				onclick={() => ($feedFilters.showUnreadOnly = !$feedFilters.showUnreadOnly)}
			>
				<span class="flex h-4 w-4 items-center justify-center rounded border {$feedFilters.showUnreadOnly ? 'border-laya-orange bg-laya-orange/20' : 'border-surface-600'}">
					{#if $feedFilters.showUnreadOnly}
						<svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
						</svg>
					{/if}
				</span>
				Unread Only
			</button>
		</div>

		<!-- Clear all -->
		{#if hasActiveFilters}
			<div class="border-t border-surface-700 pt-2">
				<button
					class="w-full rounded-md px-2 py-1 text-laya-secondary font-medium text-surface-500 transition-colors hover:text-surface-300 hover:bg-surface-700"
					onclick={() => {
						$feedFilters.statusFilters = [];
						$feedFilters.priorityFilters = [];
						$feedFilters.showArchived = false;
						$feedFilters.showBookmarked = false;
						$feedFilters.hasWorkspace = false;
						$feedFilters.showUnreadOnly = false;
						$feedFilters.spaceFilter = [];
						$feedFilters.showRelated = false;
						$feedFilters.relatedEntityIds = [];
						$feedFilters.relatedSourceHeader = '';
						$feedFilters.relatedSourceCardId = '';
					}}
				>
					Clear all filters
				</button>
			</div>
		{/if}
	</div>
{/if}
