<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	// A strip below the title bar shown while the last n8n upgrade has failed
	// (the shell rolled back to the previous n8n). It links to the Status page,
	// where the error and the "Retry upgrade on next restart" button are. The
	// dismiss button hides it until the next launch. The status is read again
	// when setup finishes, because the upgrade runs during setup.
	import { onMount } from 'svelte';
	import { setupComplete } from '$lib/stores/setup';
	import { fetchN8nRuntimeStatus, upgradeFailed, type N8nRuntimeStatus } from '$lib/utils/n8nRuntime';

	let status = $state<N8nRuntimeStatus | null>(null);
	let dismissed = $state(false);

	async function load() {
		status = await fetchN8nRuntimeStatus();
	}

	onMount(load);

	$effect(() => {
		if ($setupComplete) load();
	});
</script>

{#if upgradeFailed(status) && !dismissed}
	<div class="flex items-center justify-center gap-2 bg-laya-gold/10 border-b border-laya-gold/25 px-4 py-1.5">
		<span class="text-xs text-laya-amber">
			The n8n upgrade failed. Laya is still using n8n {status?.record?.n8n_version}.
		</span>
		<a href="/status" class="ml-1 text-xs font-medium text-laya-amber underline underline-offset-2">Status</a>
		<button
			onclick={() => (dismissed = true)}
			class="ml-2 text-surface-500 hover:text-surface-300 text-xs transition-colors"
			title="Dismiss">&times;</button
		>
	</div>
{/if}
