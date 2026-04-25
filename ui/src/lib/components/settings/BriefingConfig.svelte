<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';

	let enabled = $state(true);
	let time = $state('07:00');
	let timezone = $state('America/New_York');
	let loading = $state(true);
	let saving = $state(false);
	let saved = $state(false);
	let error = $state('');

	// Omni settings
	let omniEnabled = $state(true);
	let omniResynthesisTime = $state('17:00');
	let omniDensity = $state('compact');
	let omniTimezone = $state('America/New_York');
	let omniRollingHours = $state(4);
	let omniEventThreshold = $state(50);
	let omniSaving = $state(false);
	let omniSaved = $state(false);
	let omniError = $state('');

	// Context Association settings
	let contextAssociationEnabled = $state(true);
	let contextSaving = $state(false);
	let contextSaved = $state(false);
	let contextError = $state('');

	// Group Summaries settings
	let groupSummariesEnabled = $state(true);
	let groupSumSaving = $state(false);
	let groupSumSaved = $state(false);
	let groupSumError = $state('');

	// Debounce timers
	let briefingTimer: ReturnType<typeof setTimeout> | null = null;
	let omniTimer: ReturnType<typeof setTimeout> | null = null;

	const timezones = [
		'Pacific/Auckland',
		'Australia/Sydney',
		'Australia/Adelaide',
		'Australia/Perth',
		'Asia/Tokyo',
		'Asia/Seoul',
		'Asia/Shanghai',
		'Asia/Hong_Kong',
		'Asia/Singapore',
		'Asia/Kolkata',
		'Asia/Dubai',
		'Europe/Moscow',
		'Europe/Istanbul',
		'Europe/Helsinki',
		'Europe/Bucharest',
		'Europe/Athens',
		'Europe/Berlin',
		'Europe/Paris',
		'Europe/Amsterdam',
		'Europe/Rome',
		'Europe/Zurich',
		'Europe/London',
		'Atlantic/Reykjavik',
		'America/Sao_Paulo',
		'America/Buenos_Aires',
		'America/Halifax',
		'America/New_York',
		'America/Chicago',
		'America/Denver',
		'America/Phoenix',
		'America/Los_Angeles',
		'America/Anchorage',
		'Pacific/Honolulu',
	];

	$effect(() => {
		engineApi.getSettings().then((s) => {
			enabled = s.briefing?.enabled ?? true;
			time = s.briefing?.time ?? '07:00';
			timezone = s.briefing?.timezone ?? 'America/New_York';

			omniEnabled = s.omni?.enabled ?? true;
			omniResynthesisTime = s.omni?.resynthesis_time ?? '17:00';
			omniDensity = s.omni?.density ?? 'compact';
			omniTimezone = s.omni?.timezone ?? 'America/New_York';
			omniRollingHours = s.omni?.rolling_interval_hours ?? 4;
			omniEventThreshold = s.omni?.event_threshold ?? 50;

			contextAssociationEnabled = s.smart_grouping?.context_association ?? true;
			groupSummariesEnabled = s.group_summaries?.enabled ?? true;
			loading = false;
		});
	});

	function debouncedSaveBriefing() {
		if (briefingTimer) clearTimeout(briefingTimer);
		saving = true;
		saved = false;
		error = '';
		briefingTimer = setTimeout(() => saveBriefing(), 800);
	}

	async function saveBriefing() {
		try {
			await engineApi.updateSettings({ briefing: { enabled, time, timezone } } as never);
			saved = true;
			setTimeout(() => (saved = false), 2000);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Save failed';
		} finally {
			saving = false;
		}
	}

	function debouncedSaveOmni() {
		if (omniTimer) clearTimeout(omniTimer);
		omniSaving = true;
		omniSaved = false;
		omniError = '';
		omniTimer = setTimeout(() => saveOmni(), 800);
	}

	async function saveOmni() {
		try {
			await engineApi.updateSettings({
				omni: {
					enabled: omniEnabled,
					resynthesis_time: omniResynthesisTime,
					density: omniDensity,
					timezone: omniTimezone,
					rolling_interval_hours: omniRollingHours,
					event_threshold: omniEventThreshold
				}
			} as never);
			omniSaved = true;
			setTimeout(() => (omniSaved = false), 2000);
		} catch (e) {
			omniError = e instanceof Error ? e.message : 'Save failed';
		} finally {
			omniSaving = false;
		}
	}

	function formatTzLabel(tz: string): string {
		try {
			const now = new Date();
			const formatter = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'shortOffset' });
			const parts = formatter.formatToParts(now);
			const offset = parts.find((p) => p.type === 'timeZoneName')?.value ?? '';
			return `${tz.replace(/_/g, ' ')} (${offset})`;
		} catch {
			return tz.replace(/_/g, ' ');
		}
	}

	const previewTime = $derived.by(() => {
		try {
			const [h, m] = time.split(':').map(Number);
			const d = new Date();
			d.setHours(h, m, 0, 0);
			return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
		} catch {
			return time;
		}
	});

	const omniPreviewTime = $derived.by(() => {
		try {
			const [h, m] = omniResynthesisTime.split(':').map(Number);
			const d = new Date();
			d.setHours(h, m, 0, 0);
			return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
		} catch {
			return omniResynthesisTime;
		}
	});

	const densityDescriptions: Record<string, string> = {
		compact: 'Fits on one screen. Ultra-concise, only the most important information.',
		standard: 'Balanced detail. Covers key events with enough context to act on.',
		detailed: 'Comprehensive view. Includes secondary events and fuller context.'
	};

	function handleBriefingToggle() {
		enabled = !enabled;
		// Toggle saves immediately, no debounce needed
		saveBriefing();
	}

	function handleOmniToggle() {
		omniEnabled = !omniEnabled;
		saveOmni();
	}

	function handleContextAssociationToggle() {
		contextAssociationEnabled = !contextAssociationEnabled;
		saveContextSettings();
	}

	async function saveContextSettings() {
		contextSaving = true;
		contextError = '';
		try {
			await engineApi.updateSettings({
				smart_grouping: {
					context_association: contextAssociationEnabled,
					confidence_threshold: 0.30,
					auto_confirm_threshold: 0.20,
				}
			} as never);
			contextSaved = true;
			setTimeout(() => (contextSaved = false), 2000);
		} catch (e) {
			contextError = e instanceof Error ? e.message : 'Save failed';
		} finally {
			contextSaving = false;
		}
	}

	async function handleGroupSummariesToggle() {
		groupSummariesEnabled = !groupSummariesEnabled;
		groupSumSaving = true;
		groupSumError = '';
		try {
			await engineApi.updateSettings({
				group_summaries: { enabled: groupSummariesEnabled }
			} as never);
			groupSumSaved = true;
			setTimeout(() => (groupSumSaved = false), 2000);
		} catch (e) {
			groupSumError = e instanceof Error ? e.message : 'Save failed';
			groupSummariesEnabled = !groupSummariesEnabled;
		} finally {
			groupSumSaving = false;
		}
	}
</script>

<div class="space-y-6">
	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<h3 class="mb-1 font-medium">Daily Briefing</h3>
		<p class="mb-4 text-sm text-surface-400">
			Laya generates a daily briefing card summarising overnight activity, pending cards, and your
			calendar. Configure when this briefing runs.
		</p>

		{#if loading}
			<p class="text-sm text-surface-500">Loading…</p>
		{:else}
			<div class="space-y-4">
				<!-- Enabled toggle -->
				<div class="flex items-center justify-between rounded-md border border-surface-600 bg-surface-700/40 px-4 py-3">
					<div>
						<span class="text-sm font-medium text-surface-100">Enable daily briefing</span>
						<p class="text-xs text-surface-400">Generate a briefing card each day at the scheduled time</p>
					</div>
					<button
						class="relative h-6 w-11 rounded-full transition-colors {enabled ? 'bg-laya-orange' : 'bg-surface-600'}"
						onclick={handleBriefingToggle}
						role="switch"
						aria-checked={enabled}
						aria-label="Toggle daily briefing"
					>
						<span class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white transition-transform {enabled ? 'translate-x-5' : ''}"></span>
					</button>
				</div>

				<!-- Time and timezone -->
				<div class="grid grid-cols-[auto_1fr] items-end gap-3" class:opacity-40={!enabled}>
					<div class="flex flex-col gap-1">
						<label class="text-xs font-medium text-surface-300" for="briefing-time">
							Time
						</label>
						<input
							id="briefing-time"
							type="time"
							bind:value={time}
							oninput={() => debouncedSaveBriefing()}
							disabled={!enabled}
							class="h-9 w-36 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
						/>
					</div>

					<div class="flex flex-col gap-1">
						<label class="text-xs font-medium text-surface-300" for="briefing-tz">
							Timezone
						</label>
						<select
							id="briefing-tz"
							bind:value={timezone}
							onchange={() => debouncedSaveBriefing()}
							disabled={!enabled}
							class="h-9 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
						>
							{#each timezones as tz}
								<option value={tz}>{formatTzLabel(tz)}</option>
							{/each}
						</select>
					</div>
				</div>

				{#if error}
					<p class="text-xs text-red-400">{error}</p>
				{/if}

				{#if enabled}
					<p class="text-xs text-surface-500">
						{#if saving}
							<span class="text-laya-orange">Saving…</span>
						{:else if saved}
							<span class="text-green-400">Saved</span> —
						{/if}
						Briefing will run daily at
						<span class="text-surface-300">{previewTime}</span>
						in
						<span class="text-surface-300">{timezone.replace(/_/g, ' ')}</span>.
					</p>
				{/if}
			</div>
		{/if}
	</div>

	<!-- Context Association -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<div class="flex items-center gap-2 mb-1">
			<h3 class="font-medium">Context Association</h3>
			<span class="rounded-full border border-laya-orange/30 bg-laya-orange/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-laya-orange">Beta</span>
		</div>
		<p class="mb-4 text-sm text-surface-400">
			Automatically detect when different notifications are about the same real-world context.
			For example, a bill notification and its payment receipt will be linked together.
			Works across different senders, threads, and platforms.
		</p>

		{#if !loading}
			<div class="space-y-4">
				<!-- Context Association main toggle -->
				<div class="rounded-md border border-surface-600 bg-surface-700/40">
					<div class="flex items-center justify-between px-4 py-3">
						<div>
							<span class="text-sm font-medium text-surface-100">Enable context association</span>
							<p class="text-xs text-surface-400">Compute semantic links between related cards during event processing</p>
						</div>
						<button
							class="relative h-6 w-11 shrink-0 rounded-full transition-colors {contextAssociationEnabled ? 'bg-laya-orange' : 'bg-surface-600'}"
							onclick={handleContextAssociationToggle}
							role="switch"
							aria-checked={contextAssociationEnabled}
							aria-label="Toggle context association"
						>
							<span class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white transition-transform {contextAssociationEnabled ? 'translate-x-5' : ''}"></span>
						</button>
					</div>

					<!-- Info when disabled -->
					{#if !contextAssociationEnabled}
						<div class="border-t border-surface-600/50 px-4 py-2">
							<p class="text-[11px] text-surface-500 flex items-center gap-1.5">
								Related cards detection is disabled.
							</p>
						</div>
					{/if}
				</div>

				{#if contextError}
					<p class="text-xs text-red-400">{contextError}</p>
				{/if}
				{#if contextSaving}
					<p class="text-xs text-laya-orange">Saving...</p>
				{:else if contextSaved}
					<p class="text-xs text-green-400">Saved</p>
				{/if}
			</div>
		{/if}
	</div>

	<!-- Group Summaries -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<div class="flex items-center gap-2 mb-1">
			<h3 class="font-medium">Group Summaries</h3>
			<span class="rounded-full border border-laya-orange/30 bg-laya-orange/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-laya-orange">Beta</span>
		</div>
		<p class="mb-4 text-sm text-surface-400">
			Generate rolling AI summaries for card groups. When multiple cards share the same entity,
			Laya synthesizes them into an executive snapshot that updates as new events arrive.
		</p>

		{#if !loading}
			<div class="space-y-4">
				<div class="flex items-center justify-between rounded-md border border-surface-600 bg-surface-700/40 px-4 py-3">
					<div>
						<span class="text-sm font-medium text-surface-100">Enable group summaries</span>
						<p class="text-xs text-surface-400">Automatically summarize multi-card entity groups</p>
					</div>
					<button
						class="relative h-6 w-11 shrink-0 rounded-full transition-colors {groupSummariesEnabled ? 'bg-laya-orange' : 'bg-surface-600'}"
						onclick={handleGroupSummariesToggle}
						role="switch"
						aria-checked={groupSummariesEnabled}
						aria-label="Toggle group summaries"
					>
						<span class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white transition-transform {groupSummariesEnabled ? 'translate-x-5' : ''}"></span>
					</button>
				</div>

				{#if groupSumError}
					<p class="text-xs text-red-400">{groupSumError}</p>
				{/if}
				{#if groupSumSaving}
					<p class="text-xs text-laya-orange">Saving...</p>
				{:else if groupSumSaved}
					<p class="text-xs text-green-400">Saved</p>
				{/if}
			</div>
		{/if}
	</div>

	<!-- Omni settings -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<h3 class="mb-1 font-medium">Omni</h3>
		<p class="mb-4 text-sm text-surface-400">
			Omni maintains a rolling cross-platform summary of your professional activity.
			Configure when resynthesis runs and how detailed the summary should be.
		</p>

		{#if !loading}
			<div class="space-y-4">
				<!-- Enabled toggle -->
				<div class="flex items-center justify-between rounded-md border border-surface-600 bg-surface-700/40 px-4 py-3">
					<div>
						<span class="text-sm font-medium text-surface-100">Enable Omni</span>
						<p class="text-xs text-surface-400">Track and summarise activity across all platforms</p>
					</div>
					<button
						class="relative h-6 w-11 rounded-full transition-colors {omniEnabled ? 'bg-laya-orange' : 'bg-surface-600'}"
						onclick={handleOmniToggle}
						role="switch"
						aria-checked={omniEnabled}
						aria-label="Toggle Omni"
					>
						<span class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white transition-transform {omniEnabled ? 'translate-x-5' : ''}"></span>
					</button>
				</div>

				<div class="space-y-4" class:opacity-40={!omniEnabled}>
					<!-- Density -->
					<div class="flex flex-col gap-1.5">
						<span class="text-xs font-medium text-surface-300">Summary density</span>
						<div class="flex rounded-lg border border-surface-600 overflow-hidden w-fit">
							{#each ['compact', 'standard', 'detailed'] as opt}
								<button
									class="px-3 py-1.5 text-sm font-medium transition-colors
										{omniDensity === opt
											? 'bg-laya-orange/15 text-laya-orange'
											: 'text-surface-400 hover:text-surface-200 hover:bg-surface-700'}"
									onclick={() => { omniDensity = opt; debouncedSaveOmni(); }}
									disabled={!omniEnabled}
								>
									{opt.charAt(0).toUpperCase() + opt.slice(1)}
								</button>
							{/each}
						</div>
						<p class="text-xs text-surface-500">{densityDescriptions[omniDensity]}</p>
					</div>

					<!-- Resynthesis time and timezone -->
					<div class="grid grid-cols-[auto_1fr] items-end gap-3">
						<div class="flex flex-col gap-1">
							<label class="text-xs font-medium text-surface-300" for="omni-time">
								Resynthesis time
							</label>
							<input
								id="omni-time"
								type="time"
								bind:value={omniResynthesisTime}
								oninput={() => debouncedSaveOmni()}
								disabled={!omniEnabled}
								class="h-9 w-36 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
							/>
						</div>

						<div class="flex flex-col gap-1">
							<label class="text-xs font-medium text-surface-300" for="omni-tz">
								Timezone
							</label>
							<select
								id="omni-tz"
								bind:value={omniTimezone}
								onchange={() => debouncedSaveOmni()}
								disabled={!omniEnabled}
								class="h-9 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
							>
								{#each timezones as tz}
									<option value={tz}>{formatTzLabel(tz)}</option>
								{/each}
							</select>
						</div>
					</div>

					<!-- Rolling resynthesis controls -->
					<div class="grid grid-cols-[auto_auto] items-end gap-3">
						<div class="flex flex-col gap-1">
							<label class="text-xs font-medium text-surface-300" for="omni-rolling">
								Rolling interval
							</label>
							<select
								id="omni-rolling"
								bind:value={omniRollingHours}
								onchange={() => debouncedSaveOmni()}
								disabled={!omniEnabled}
								class="h-9 w-36 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
							>
								<option value={0}>Off</option>
								<option value={1}>Every 1h</option>
								<option value={2}>Every 2h</option>
								<option value={4}>Every 4h</option>
								<option value={6}>Every 6h</option>
								<option value={8}>Every 8h</option>
							</select>
						</div>

						<div class="flex flex-col gap-1">
							<label class="text-xs font-medium text-surface-300" for="omni-threshold">
								Event threshold
							</label>
							<input
								id="omni-threshold"
								type="number"
								min="0"
								max="100"
								step="10"
								bind:value={omniEventThreshold}
								oninput={() => {
									// Mirror the server-side clamp [0, 100] so the UI can't
									// submit a value that will be silently rewritten.
									if (omniEventThreshold < 0) omniEventThreshold = 0;
									if (omniEventThreshold > 100) omniEventThreshold = 100;
									debouncedSaveOmni();
								}}
								disabled={!omniEnabled}
								class="h-9 w-36 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
								placeholder="0 = off"
							/>
						</div>
					</div>

					<p class="text-xs text-surface-500">
						Resynthesis triggers: daily at <span class="text-surface-300">{omniPreviewTime}</span>{omniRollingHours > 0 ? `, every ${omniRollingHours}h` : ''}{omniEventThreshold > 0 ? `, or after ${omniEventThreshold} new events` : ''} — whichever comes first.
						Synthesis is skipped automatically if no new cards have arrived since the last run.
					</p>

					{#if omniError}
						<p class="text-xs text-red-400">{omniError}</p>
					{/if}

					{#if omniSaving}
						<p class="text-xs text-laya-orange">Saving…</p>
					{:else if omniSaved}
						<p class="text-xs text-green-400">Saved</p>
					{/if}
				</div>
			</div>
		{/if}
	</div>
</div>
