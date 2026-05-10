<script lang="ts">
	import { glassTheme } from '$lib/stores/glassTheme';

	const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent);
	const mod = isMac ? '⌘' : 'Ctrl+';

	const keybindings = [
		{ key: 'A', description: 'Open run agent dialog', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: 'B', description: 'Toggle bookmarks filter', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: 'C', description: 'Open compose modal', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: 'L', description: 'Toggle chat panel', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: `${mod}O`, description: 'Go to Omni', scope: 'Global', note: '' },
		{ key: `${mod}P`, description: 'Go to Pulse (feed)', scope: 'Global', note: '' },
		{ key: 'R', description: 'Toggle recent items', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: 'S', description: 'Toggle day summary', scope: 'Global', note: 'Only when not focused on an input field' },
		{ key: `${mod}D`, description: 'Toggle card descriptions', scope: 'Global', note: '' },
		{ key: `${isMac ? '⇧⌘' : 'Ctrl+Shift+'}D`, description: 'Toggle compact / relaxed card layout', scope: 'Global', note: '' },
		{ key: 'Esc', description: 'Dismiss popover / dropdown', scope: 'Modals', note: 'Closes auto-suggest or directory dropdown first' },
		{ key: `${mod}.`, description: 'Close modal', scope: 'Modals', note: 'Compose and Run Agent modals' },
		{ key: `${mod}↵`, description: 'Send compose form', scope: 'Compose modal', note: '' },
		{ key: `${mod}F`, description: 'Focus search box', scope: 'Feed', note: '' },
		{ key: `${mod}S`, description: 'Go to Coherence and focus search', scope: 'Global', note: '' },
		{ key: `${mod}[`, description: 'Navigate back', scope: 'Global', note: '' },
		{ key: `${mod}]`, description: 'Navigate forward', scope: 'Global', note: '' },
	];
</script>

<div class="space-y-8">
	<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-6">
		<h3 class="mb-1 text-laya-heading font-semibold text-surface-50">Keyboard Shortcuts</h3>
		<p class="mb-5 text-laya-base text-surface-400">View all available keyboard shortcuts across the application.</p>

		<div class="overflow-hidden rounded-lg border {$glassTheme ? 'border-white/[0.06]' : 'border-surface-700'}">
			<table class="w-full text-laya-base">
				<thead>
					<tr class="border-b {$glassTheme ? 'border-white/[0.06] bg-white/[0.03]' : 'border-surface-700 bg-surface-900/50'}">
						<th class="px-4 py-2.5 text-left text-laya-secondary font-medium uppercase tracking-wider text-surface-400">Key</th>
						<th class="px-4 py-2.5 text-left text-laya-secondary font-medium uppercase tracking-wider text-surface-400">Action</th>
						<th class="px-4 py-2.5 text-left text-laya-secondary font-medium uppercase tracking-wider text-surface-400">Scope</th>
					</tr>
				</thead>
				<tbody>
					{#each keybindings as binding, i}
						<tr class="border-b last:border-0 {$glassTheme ? 'border-white/[0.04]' : 'border-surface-700/50'} {$glassTheme ? (i % 2 === 0 ? 'bg-white/[0.02]' : 'bg-transparent') : (i % 2 === 0 ? 'bg-surface-800' : 'bg-surface-850/30')}">
							<td class="px-4 py-2.5">
								<kbd class="inline-flex min-w-[2rem] items-center justify-center rounded-md border px-2 py-0.5 font-mono text-laya-secondary text-surface-200 {$glassTheme ? 'border-white/[0.12] bg-white/[0.06]' : 'border-surface-600 bg-surface-900'}">{binding.key}</kbd>
							</td>
							<td class="px-4 py-2.5 text-surface-200">
								{binding.description}
								{#if binding.note}
									<span class="ml-1 text-laya-secondary text-surface-500">({binding.note})</span>
								{/if}
							</td>
							<td class="px-4 py-2.5">
								<span class="rounded-full {$glassTheme ? 'bg-white/[0.06]' : 'bg-surface-700/50'} px-2 py-0.5 text-laya-secondary text-surface-400">{binding.scope}</span>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</div>
</div>
