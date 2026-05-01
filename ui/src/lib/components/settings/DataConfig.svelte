<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';

	type Section = 'card' | 'chat' | 'audit' | 'omni' | 'ingestion';

	let retentionDays = $state(90);
	let chatRetentionDays = $state(90);
	let auditRetentionDays = $state(90);
	let omniRetentionDays = $state(30);
	let ingestionErrorsRetentionDays = $state(30);
	let loading = $state(true);
	let savingSection = $state<Section | null>(null);
	let savedSection = $state<Section | null>(null);
	let errorSection = $state<Section | null>(null);
	let error = $state('');

	let saveTimer: ReturnType<typeof setTimeout> | null = null;
	let savedClearTimer: ReturnType<typeof setTimeout> | null = null;

	$effect(() => {
		engineApi.getSettings().then((s) => {
			retentionDays = s.retention?.card_retention_days ?? 90;
			chatRetentionDays = s.retention?.chat_retention_days ?? 90;
			auditRetentionDays = s.retention?.audit_retention_days ?? 90;
			omniRetentionDays = s.retention?.omni_retention_days ?? 30;
			ingestionErrorsRetentionDays = s.retention?.ingestion_errors_retention_days ?? 30;
			loading = false;
		});
	});

	function debouncedSave(section: Section) {
		if (saveTimer) clearTimeout(saveTimer);
		if (savedClearTimer) clearTimeout(savedClearTimer);
		savingSection = section;
		savedSection = null;
		errorSection = null;
		error = '';
		saveTimer = setTimeout(() => save(section), 800);
	}

	async function save(section: Section) {
		try {
			await engineApi.updateSettings({
				retention: {
					card_retention_days: retentionDays,
					chat_retention_days: chatRetentionDays,
					audit_retention_days: auditRetentionDays,
					omni_retention_days: omniRetentionDays,
					ingestion_errors_retention_days: ingestionErrorsRetentionDays
				}
			} as never);
			savedSection = section;
			savedClearTimer = setTimeout(() => (savedSection = null), 2000);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Save failed';
			errorSection = section;
		} finally {
			if (savingSection === section) savingSection = null;
		}
	}
</script>

<div class="space-y-6">
	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<h3 class="mb-1 font-medium">Card Retention</h3>
		<p class="mb-4 text-sm text-surface-400">
			Cards in archived, dismissed, completed, or failed states that are older than the retention
			period are automatically deleted each day. Active cards are never auto-deleted.
		</p>

		{#if loading}
			<p class="text-sm text-surface-500">Loading…</p>
		{:else}
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
					oninput={() => debouncedSave('card')}
					class="h-9 w-32 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none"
				/>
			</div>

			{#if errorSection === 'card' && error}
				<p class="mt-2 text-xs text-red-400">{error}</p>
			{/if}

			<p class="mt-3 text-xs text-surface-500">
				{#if savingSection === 'card'}
					<span class="text-laya-orange">Saving…</span>
				{:else if savedSection === 'card'}
					<span class="text-green-400">Saved</span> —
				{/if}
				Default: 90 days. Cards created before
				<span class="text-surface-300">
					{new Date(Date.now() - retentionDays * 86400000).toLocaleDateString()}
				</span>
				would be eligible for deletion.
			</p>
		{/if}
	</div>

	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<h3 class="mb-1 font-medium">Chat Retention</h3>
		<p class="mb-4 text-sm text-surface-400">
			Conversations that have been idle for longer than the retention period are automatically
			deleted each day, along with all their messages.
		</p>

		{#if !loading}
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
					oninput={() => debouncedSave('chat')}
					class="h-9 w-32 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none"
				/>
			</div>

			{#if errorSection === 'chat' && error}
				<p class="mt-2 text-xs text-red-400">{error}</p>
			{/if}

			<p class="mt-3 text-xs text-surface-500">
				{#if savingSection === 'chat'}
					<span class="text-laya-orange">Saving…</span>
				{:else if savedSection === 'chat'}
					<span class="text-green-400">Saved</span> —
				{/if}
				Default: 90 days. Conversations last active before
				<span class="text-surface-300">
					{new Date(Date.now() - chatRetentionDays * 86400000).toLocaleDateString()}
				</span>
				would be eligible for deletion.
			</p>
		{/if}
	</div>

	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<h3 class="mb-1 font-medium">Audit Log Retention</h3>
		<p class="mb-4 text-sm text-surface-400">
			LLM call audit logs older than the retention period are automatically deleted each day.
		</p>

		{#if !loading}
			<div class="flex flex-col gap-1">
				<label class="text-xs font-medium text-surface-300" for="audit-retention-days">
					Retention period (days)
				</label>
				<input
					id="audit-retention-days"
					type="number"
					min="1"
					max="3650"
					bind:value={auditRetentionDays}
					oninput={() => debouncedSave('audit')}
					class="h-9 w-32 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none"
				/>
			</div>

			{#if errorSection === 'audit' && error}
				<p class="mt-2 text-xs text-red-400">{error}</p>
			{/if}

			<p class="mt-3 text-xs text-surface-500">
				{#if savingSection === 'audit'}
					<span class="text-laya-orange">Saving…</span>
				{:else if savedSection === 'audit'}
					<span class="text-green-400">Saved</span> —
				{/if}
				Default: 90 days.
			</p>
		{/if}
	</div>

	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<h3 class="mb-1 font-medium">Omni Retention</h3>
		<p class="mb-4 text-sm text-surface-400">
			Omni timeline snapshots older than the retention period are automatically deleted each day.
			The latest snapshot per space is always preserved.
		</p>

		{#if !loading}
			<div class="flex flex-col gap-1">
				<label class="text-xs font-medium text-surface-300" for="omni-retention-days">
					Retention period (days)
				</label>
				<input
					id="omni-retention-days"
					type="number"
					min="1"
					max="365"
					bind:value={omniRetentionDays}
					oninput={() => debouncedSave('omni')}
					class="h-9 w-32 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none"
				/>
			</div>

			{#if errorSection === 'omni' && error}
				<p class="mt-2 text-xs text-red-400">{error}</p>
			{/if}

			<p class="mt-3 text-xs text-surface-500">
				{#if savingSection === 'omni'}
					<span class="text-laya-orange">Saving…</span>
				{:else if savedSection === 'omni'}
					<span class="text-green-400">Saved</span> —
				{/if}
				Default: 30 days.
			</p>
		{/if}
	</div>

	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<h3 class="mb-1 font-medium">Ingestion Errors Retention</h3>
		<p class="mb-4 text-sm text-surface-400">
			Captured n8n ingestion failures older than the retention period are automatically deleted
			each day. Cleared errors are also subject to this retention.
		</p>

		{#if !loading}
			<div class="flex flex-col gap-1">
				<label class="text-xs font-medium text-surface-300" for="ingestion-retention-days">
					Retention period (days)
				</label>
				<input
					id="ingestion-retention-days"
					type="number"
					min="1"
					max="365"
					bind:value={ingestionErrorsRetentionDays}
					oninput={() => debouncedSave('ingestion')}
					class="h-9 w-32 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none"
				/>
			</div>

			{#if errorSection === 'ingestion' && error}
				<p class="mt-2 text-xs text-red-400">{error}</p>
			{/if}

			<p class="mt-3 text-xs text-surface-500">
				{#if savingSection === 'ingestion'}
					<span class="text-laya-orange">Saving…</span>
				{:else if savedSection === 'ingestion'}
					<span class="text-green-400">Saved</span> —
				{/if}
				Default: 30 days.
			</p>
		{/if}
	</div>

	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<h3 class="mb-1 font-medium">Manual Deletion</h3>
		<p class="text-sm text-surface-400">
			To delete a card manually, first <span class="text-surface-300">archive</span> it — a trash
			icon will appear on the card. Clicking it opens a confirmation before permanently removing the
			card and all its related data.
		</p>
	</div>
</div>
