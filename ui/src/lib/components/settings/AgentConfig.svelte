<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';

	const agents = [
		{ value: 'claude_code', label: 'Claude Code', description: 'Anthropic CLI — structured JSON streaming, approval prompts' },
		{ value: 'gemini_cli', label: 'Gemini CLI', description: 'Google CLI — raw line-by-line output' },
		{ value: 'codex_cli', label: 'Codex CLI', description: 'OpenAI CLI — raw line-by-line output' }
	];

	let selected = $state('claude_code');
	let loading = $state(true);
	let saving = $state(false);
	let error = $state<string | null>(null);

	onMount(async () => {
		try {
			const settings = await engineApi.getSettings();
			selected = settings.coding_agent || 'claude_code';
		} catch {
			error = 'Failed to load settings';
		} finally {
			loading = false;
		}
	});

	async function selectAgent(value: string) {
		selected = value;
		saving = true;
		error = null;
		try {
			await engineApi.updateSettings({ coding_agent: value });
		} catch {
			error = 'Failed to save coding agent preference';
		} finally {
			saving = false;
		}
	}
</script>

{#if loading}
	<div class="text-surface-400">Loading agent settings...</div>
{:else}
	{#if error}
		<div class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-2 text-sm text-red-300">{error}</div>
	{/if}

	<div class="rounded-xl border border-surface-700 bg-surface-800 p-4">
		<h3 class="mb-1 text-sm font-medium">Coding Agent</h3>
		<p class="mb-4 text-xs text-surface-400">Select which CLI coding agent Laya uses for ENGINEER tasks</p>

		<div class="space-y-2">
			{#each agents as agent}
				<button
					class="flex w-full items-center gap-3 rounded-lg border px-4 py-3 text-left transition-colors
						{selected === agent.value
							? 'border-blue-500 bg-blue-500/10'
							: 'border-surface-600 bg-surface-900 hover:border-surface-500'}"
					onclick={() => selectAgent(agent.value)}
					disabled={saving}
				>
					<div class="flex h-4 w-4 items-center justify-center rounded-full border-2
						{selected === agent.value ? 'border-blue-500' : 'border-surface-500'}">
						{#if selected === agent.value}
							<div class="h-2 w-2 rounded-full bg-blue-500"></div>
						{/if}
					</div>
					<div>
						<div class="text-sm font-medium">{agent.label}</div>
						<div class="text-xs text-surface-400">{agent.description}</div>
					</div>
				</button>
			{/each}
		</div>

		{#if saving}
			<div class="mt-3 text-xs text-surface-400">Saving...</div>
		{/if}
	</div>
{/if}
