<script lang="ts">
	import { engineApi } from '$lib/api/engine';

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
</script>

<div class="space-y-6">
	<div class="rounded-lg border border-surface-700 bg-surface-800 p-5">
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

	<!-- Omni settings -->
	<div class="rounded-lg border border-surface-700 bg-surface-800 p-5">
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
								step="10"
								bind:value={omniEventThreshold}
								oninput={() => debouncedSaveOmni()}
								disabled={!omniEnabled}
								class="h-9 w-36 rounded-md border border-surface-600 bg-surface-700 px-3 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
								placeholder="0 = off"
							/>
						</div>
					</div>

					<p class="text-xs text-surface-500">
						Resynthesis triggers: daily at <span class="text-surface-300">{omniPreviewTime}</span>{omniRollingHours > 0 ? `, every ${omniRollingHours}h` : ''}{omniEventThreshold > 0 ? `, or after ${omniEventThreshold} new events` : ''} — whichever comes first.
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
