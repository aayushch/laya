<script lang="ts">
	import { engineApi } from '$lib/api/engine';

	let retentionDays = $state(90);
	let chatRetentionDays = $state(90);
	let loading = $state(true);
	let saving = $state(false);
	let saved = $state(false);
	let error = $state('');

	$effect(() => {
		engineApi.getSettings().then((s) => {
			retentionDays = s.retention?.card_retention_days ?? 90;
			chatRetentionDays = s.retention?.chat_retention_days ?? 90;
			loading = false;
		});
	});

	async function save() {
		saving = true;
		error = '';
		try {
			await engineApi.updateSettings({
				retention: {
					card_retention_days: retentionDays,
					chat_retention_days: chatRetentionDays
				}
			} as never);
			saved = true;
			setTimeout(() => (saved = false), 2000);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Save failed';
		} finally {
			saving = false;
		}
	}
</script>

<div class="space-y-6">
	<div class="rounded-lg border border-surface-700 bg-surface-800 p-5">
		<h3 class="mb-1 font-medium">Card Retention</h3>
		<p class="mb-4 text-sm text-surface-400">
			Cards in archived, dismissed, completed, or failed states that are older than the retention
			period are automatically deleted each day. Active cards are never auto-deleted.
		</p>

		{#if loading}
			<p class="text-sm text-surface-500">Loading…</p>
		{:else}
			<div class="flex items-end gap-4">
				<div class="flex flex-col gap-1">
					<label class="text-xs font-medium text-surface-300" for="retention-days">
						Retention period (days)
					</label>
					<input
						id="retention-days"
						type="number"
						min="1"
						max="3650"
						bind:value={retentionDays}
						class="w-32 rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none"
					/>
				</div>
				<button
					class="rounded-md bg-laya-orange/80 px-4 py-1.5 text-sm font-medium text-surface-900 transition-colors hover:bg-laya-orange disabled:opacity-50"
					onclick={save}
					disabled={saving}
				>
					{saving ? 'Saving…' : saved ? 'Saved ✓' : 'Save'}
				</button>
			</div>

			{#if error}
				<p class="mt-2 text-xs text-red-400">{error}</p>
			{/if}

			<p class="mt-3 text-xs text-surface-500">
				Default: 90 days. Cards created before
				<span class="text-surface-300">
					{new Date(Date.now() - retentionDays * 86400000).toLocaleDateString()}
				</span>
				would be eligible for deletion.
			</p>
		{/if}
	</div>

	<div class="rounded-lg border border-surface-700 bg-surface-800 p-5">
		<h3 class="mb-1 font-medium">Chat Retention</h3>
		<p class="mb-4 text-sm text-surface-400">
			Conversations that have been idle for longer than the retention period are automatically
			deleted each day, along with all their messages.
		</p>

		{#if !loading}
			<div class="flex items-end gap-4">
				<div class="flex flex-col gap-1">
					<label class="text-xs font-medium text-surface-300" for="chat-retention-days">
						Retention period (days)
					</label>
					<input
						id="chat-retention-days"
						type="number"
						min="1"
						max="3650"
						bind:value={chatRetentionDays}
						class="w-32 rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none"
					/>
				</div>
				<button
					class="rounded-md bg-laya-orange/80 px-4 py-1.5 text-sm font-medium text-surface-900 transition-colors hover:bg-laya-orange disabled:opacity-50"
					onclick={save}
					disabled={saving}
				>
					{saving ? 'Saving…' : saved ? 'Saved ✓' : 'Save'}
				</button>
			</div>

			<p class="mt-3 text-xs text-surface-500">
				Default: 90 days. Conversations last active before
				<span class="text-surface-300">
					{new Date(Date.now() - chatRetentionDays * 86400000).toLocaleDateString()}
				</span>
				would be eligible for deletion.
			</p>
		{/if}
	</div>

	<div class="rounded-lg border border-surface-700 bg-surface-800 p-5">
		<h3 class="mb-1 font-medium">Manual Deletion</h3>
		<p class="text-sm text-surface-400">
			To delete a card manually, first <span class="text-surface-300">archive</span> it — a trash
			icon will appear on the card. Clicking it opens a confirmation before permanently removing the
			card and all its related data.
		</p>
	</div>
</div>
