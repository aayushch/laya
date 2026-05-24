<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { glassTheme } from '$lib/stores/glassTheme';
	import {
		updateState,
		checkForUpdate,
		downloadAndInstall,
		installAndRelaunch,
		type CheckResult
	} from '$lib/stores/updater';

	let appVersion = $state<string>('');
	let lastResult = $state<CheckResult | null>(null);

	$effect(() => {
		import('@tauri-apps/api/app')
			.then((m) => m.getVersion())
			.then((v) => (appVersion = v))
			.catch(() => {});
	});

	async function onCheck() {
		lastResult = null;
		const result = await checkForUpdate();
		lastResult = result;
	}

	function formatLastChecked(ts: number | null): string {
		if (!ts) return 'Never';
		const diff = Date.now() - ts;
		if (diff < 60_000) return 'Just now';
		if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} min ago`;
		if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} hr ago`;
		return new Date(ts).toLocaleString();
	}
</script>

<div class="space-y-6">
	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<h3 class="mb-1 text-laya-heading font-medium">About Laya</h3>
		<p class="mb-4 text-laya-base text-surface-400">
			Your AI command center for professional life.
		</p>

		<dl class="space-y-2 text-laya-base">
			<div class="flex items-center justify-between">
				<dt class="text-surface-400">Version</dt>
				<dd class="text-surface-200 font-mono">{appVersion || '—'}</dd>
			</div>
			<div class="flex items-center justify-between">
				<dt class="text-surface-400">License</dt>
				<dd class="text-surface-200">Apache 2.0</dd>
			</div>
		</dl>
	</div>

	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<div class="mb-1 flex items-center justify-between">
			<h3 class="text-laya-heading font-medium">Updates</h3>
			<span class="text-laya-micro text-surface-500">
				Last checked: {formatLastChecked($updateState.lastCheckedAt)}
			</span>
		</div>
		<p class="mb-4 text-laya-base text-surface-400">
			Laya checks for updates automatically every couple of hours while the app is open. You can
			also check manually here.
		</p>

		{#if $updateState.ready}
			<div class="flex items-center gap-3">
				<span class="text-laya-base text-laya-orange">
					Update v{$updateState.version} is ready to install.
				</span>
				<button
					onclick={installAndRelaunch}
					class="rounded-md bg-laya-orange/20 px-3 py-1 text-laya-base font-medium text-laya-orange hover:bg-laya-orange/30 transition-colors"
				>
					Restart Now
				</button>
			</div>
		{:else if $updateState.downloading}
			<div class="flex items-center gap-3">
				<span class="text-laya-base text-laya-orange">Downloading update…</span>
				<div class="h-1 w-32 rounded-full bg-surface-700 overflow-hidden">
					<div
						class="h-full rounded-full bg-laya-orange transition-all"
						style="width: {$updateState.progress}%"
					></div>
				</div>
				<span class="text-laya-secondary text-surface-400">{$updateState.progress}%</span>
			</div>
		{:else if $updateState.available}
			<div class="flex items-center gap-3">
				<span class="text-laya-base text-laya-orange">
					v{$updateState.version} is available.
				</span>
				<button
					onclick={downloadAndInstall}
					class="rounded-md bg-laya-orange/20 px-3 py-1 text-laya-base font-medium text-laya-orange hover:bg-laya-orange/30 transition-colors"
				>
					Download &amp; Install
				</button>
			</div>
		{:else}
			<div class="flex items-center gap-3">
				<button
					onclick={onCheck}
					disabled={$updateState.checking}
					class="rounded-md border border-surface-600 px-3 py-1 text-laya-base text-surface-300 transition-colors hover:border-surface-500 hover:text-surface-100 disabled:opacity-50"
				>
					{$updateState.checking ? 'Checking…' : 'Check for updates'}
				</button>
				{#if lastResult === 'up-to-date' && !$updateState.checking}
					<span class="text-laya-base text-green-400">You're on the latest version.</span>
				{:else if lastResult === 'error' || $updateState.error}
					<span class="text-laya-base text-red-400">
						{$updateState.error || 'Check failed. Try again later.'}
					</span>
				{/if}
			</div>
		{/if}
	</div>
</div>
