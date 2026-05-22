<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { updateState, downloadAndInstall, installAndRelaunch } from '$lib/stores/updater';

	let dismissed = $state(false);
</script>

{#if $updateState.available && !dismissed}
	<div class="flex items-center justify-center gap-2 bg-laya-orange/10 border-b border-laya-orange/25 px-4 py-1.5">
		{#if $updateState.ready}
			<span class="text-xs text-laya-orange">Update ready — restart to apply v{$updateState.version}</span>
			<button
				onclick={installAndRelaunch}
				class="ml-1 rounded-md bg-laya-orange/20 px-2.5 py-0.5 text-xs font-medium text-laya-orange hover:bg-laya-orange/30 transition-colors"
			>
				Restart Now
			</button>
		{:else if $updateState.downloading}
			<span class="text-xs text-laya-orange">Downloading update…</span>
			<div class="h-1 w-24 rounded-full bg-surface-700 overflow-hidden">
				<div
					class="h-full rounded-full bg-laya-orange transition-all"
					style="width: {$updateState.progress}%"
				></div>
			</div>
			<span class="text-xs text-surface-400">{$updateState.progress}%</span>
		{:else if $updateState.error}
			<span class="text-xs text-red-400">Update failed: {$updateState.error}</span>
		{:else}
			<span class="text-xs text-laya-orange">Laya v{$updateState.version} is available</span>
			<button
				onclick={downloadAndInstall}
				class="ml-1 rounded-md bg-laya-orange/20 px-2.5 py-0.5 text-xs font-medium text-laya-orange hover:bg-laya-orange/30 transition-colors"
			>
				Update
			</button>
		{/if}
		<button
			onclick={() => (dismissed = true)}
			class="ml-2 text-surface-500 hover:text-surface-300 text-xs transition-colors"
			title="Dismiss">&times;</button
		>
	</div>
{/if}
