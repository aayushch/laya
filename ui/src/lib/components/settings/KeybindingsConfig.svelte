<script lang="ts">
	const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent);
	const mod = isMac ? '⌘' : 'Ctrl+';

	const keybindings = [
		{ key: 'A', description: 'Open run agent dialog', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: 'B', description: 'Toggle bookmarks filter', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: 'C', description: 'Open compose modal', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: 'L', description: 'Toggle chat panel', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: 'O', description: 'Go to Omni', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: 'P', description: 'Go to Pulse (feed)', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: 'R', description: 'Toggle recent items', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: 'S', description: 'Toggle day summary', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: `${mod}F`, description: 'Focus search box', scope: 'Feed', note: '' },
		{ key: `${mod}S`, description: 'Go to Coherence and focus search', scope: 'Global', note: '' },
	];
</script>

<div class="space-y-8">
	<div class="rounded-xl border border-surface-700 bg-surface-800 p-6">
		<h3 class="mb-1 font-semibold text-surface-50">Keyboard Shortcuts</h3>
		<p class="mb-5 text-sm text-surface-400">View all available keyboard shortcuts across the application.</p>

		<div class="overflow-hidden rounded-lg border border-surface-700">
			<table class="w-full text-sm">
				<thead>
					<tr class="border-b border-surface-700 bg-surface-900/50">
						<th class="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-surface-400">Key</th>
						<th class="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-surface-400">Action</th>
						<th class="px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wider text-surface-400">Scope</th>
					</tr>
				</thead>
				<tbody>
					{#each keybindings as binding, i}
						<tr class="border-b border-surface-700/50 last:border-0 {i % 2 === 0 ? 'bg-surface-800' : 'bg-surface-850/30'}">
							<td class="px-4 py-2.5">
								<kbd class="inline-flex min-w-[2rem] items-center justify-center rounded-md border border-surface-600 bg-surface-900 px-2 py-0.5 font-mono text-xs text-surface-200">{binding.key}</kbd>
							</td>
							<td class="px-4 py-2.5 text-surface-200">
								{binding.description}
								{#if binding.note}
									<span class="ml-1 text-xs text-surface-500">({binding.note})</span>
								{/if}
							</td>
							<td class="px-4 py-2.5">
								<span class="rounded-full bg-surface-700/50 px-2 py-0.5 text-xs text-surface-400">{binding.scope}</span>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</div>
</div>
