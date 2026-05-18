<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	import Dropdown from '$lib/components/Dropdown.svelte';

	let strictnessTooltip = $state<{ text: string; top: number; left: number } | null>(null);

	function showStrictnessTooltip(e: MouseEvent) {
		const el = e.currentTarget as HTMLElement;
		const rect = el.getBoundingClientRect();
		let text = '';
		if (contextStrictness === 'strict') {
			text = 'Links the exact same issue across different platforms. Requires shared identifiers (ticket numbers, service names). Same-platform matches are excluded.';
		} else if (contextStrictness === 'balanced') {
			text = 'Links notifications about the same broader context or topic. Works across and within platforms.';
		} else if (contextStrictness === 'lenient') {
			text = 'Links notifications that could provide useful context for each other. Broader matching for discovery.';
		} else {
			text = 'Custom configuration. Advanced settings below control matching behavior.';
		}
		strictnessTooltip = { text, top: rect.bottom + 6, left: rect.left };
	}

	function hideStrictnessTooltip() { strictnessTooltip = null; }

	let enabled = $state(true);
	let time = $state('07:00');
	let timezone = $state('America/New_York');
	let perSpace = $state(false);
	let spaceCount = $state(0);
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
	let contextStrictness = $state<'strict' | 'balanced' | 'lenient' | 'custom'>('strict');
	let contextShowAdvanced = $state(false);
	let contextConfidence = $state(0.15);
	let contextAutoConfirm = $state<number | null>(null);
	let contextCentroid = $state(0.18);
	let contextCrossPlatformRequired = $state(true);
	let contextEntityRefOverlap = $state<'hard_gate' | 'soft_boost' | 'disabled'>('hard_gate');
	let contextAlwaysLlm = $state(true);
	let contextSaving = $state(false);
	let contextSaved = $state(false);
	let contextError = $state('');

	const CONTEXT_PRESETS = {
		strict: {
			confidence_threshold: 0.15,
			auto_confirm_threshold: null,
			centroid_threshold: 0.18,
			cross_platform_required: true,
			entity_ref_overlap_mode: 'hard_gate' as const,
			always_llm: true,
		},
		balanced: {
			confidence_threshold: 0.22,
			auto_confirm_threshold: 0.10,
			centroid_threshold: 0.25,
			cross_platform_required: false,
			entity_ref_overlap_mode: 'soft_boost' as const,
			always_llm: false,
		},
		lenient: {
			confidence_threshold: 0.35,
			auto_confirm_threshold: 0.18,
			centroid_threshold: 0.35,
			cross_platform_required: false,
			entity_ref_overlap_mode: 'disabled' as const,
			always_llm: false,
		},
	};

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
		Promise.all([
			engineApi.getSettings(),
			engineApi.getSpaces(),
		]).then(([s, spacesResp]) => {
			enabled = s.briefing?.enabled ?? true;
			time = s.briefing?.time ?? '07:00';
			timezone = s.briefing?.timezone ?? 'America/New_York';
			perSpace = s.briefing?.per_space ?? false;

			spaceCount = spacesResp.spaces?.length ?? 0;

			omniEnabled = s.omni?.enabled ?? true;
			omniResynthesisTime = s.omni?.resynthesis_time ?? '17:00';
			omniDensity = s.omni?.density ?? 'compact';
			omniTimezone = s.omni?.timezone ?? 'America/New_York';
			omniRollingHours = s.omni?.rolling_interval_hours ?? 4;
			omniEventThreshold = s.omni?.event_threshold ?? 50;

			contextAssociationEnabled = s.smart_grouping?.context_association ?? true;
			contextStrictness = s.smart_grouping?.strictness ?? 'strict';
			contextConfidence = s.smart_grouping?.confidence_threshold ?? 0.15;
			contextAutoConfirm = s.smart_grouping?.auto_confirm_threshold ?? null;
			contextCentroid = s.smart_grouping?.centroid_threshold ?? 0.18;
			contextCrossPlatformRequired = s.smart_grouping?.cross_platform_required ?? true;
			contextEntityRefOverlap = s.smart_grouping?.entity_ref_overlap_mode ?? 'hard_gate';
			contextAlwaysLlm = s.smart_grouping?.always_llm ?? true;
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
			await engineApi.updateSettings({ briefing: { enabled, time, timezone, per_space: perSpace } } as never);
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
		saveBriefing();
	}

	function handlePerSpaceToggle() {
		perSpace = !perSpace;
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

	function selectContextPreset(preset: 'strict' | 'balanced' | 'lenient') {
		contextStrictness = preset;
		const p = CONTEXT_PRESETS[preset];
		contextConfidence = p.confidence_threshold;
		contextAutoConfirm = p.auto_confirm_threshold;
		contextCentroid = p.centroid_threshold;
		contextCrossPlatformRequired = p.cross_platform_required;
		contextEntityRefOverlap = p.entity_ref_overlap_mode;
		contextAlwaysLlm = p.always_llm;
		saveContextSettings();
	}

	function handleAdvancedChange() {
		contextStrictness = 'custom';
		saveContextSettings();
	}

	async function saveContextSettings() {
		contextSaving = true;
		contextError = '';
		try {
			const payload: Record<string, unknown> = {
				context_association: contextAssociationEnabled,
				strictness: contextStrictness,
			};
			if (contextStrictness === 'custom') {
				payload.confidence_threshold = contextConfidence;
				payload.auto_confirm_threshold = contextAutoConfirm;
				payload.centroid_threshold = contextCentroid;
				payload.cross_platform_required = contextCrossPlatformRequired;
				payload.entity_ref_overlap_mode = contextEntityRefOverlap;
				payload.always_llm = contextAlwaysLlm;
			}
			await engineApi.updateSettings({ smart_grouping: payload } as never);
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
		<div class="mb-1 flex items-center justify-between">
			<h3 class="text-laya-heading font-medium">Daily Briefing</h3>
			{#if saving}
				<span class="text-laya-micro text-laya-orange">Saving…</span>
			{:else if saved}
				<span class="text-laya-micro text-green-400">Saved</span>
			{/if}
		</div>
		<p class="mb-4 text-laya-base text-surface-400">
			Laya generates a daily briefing card summarising overnight activity, pending cards, and your
			calendar. Configure when this briefing runs.
		</p>

		{#if loading}
			<p class="text-laya-base text-surface-500">Loading…</p>
		{:else}
			<div class="space-y-4">
				<!-- Enabled toggle -->
				<div class="flex items-center justify-between rounded-md border {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-600 bg-surface-700/40'} px-4 py-3">
					<div>
						<span class="text-laya-base font-medium text-surface-100">Enable daily briefing</span>
						<p class="text-laya-secondary text-surface-400">Generate a briefing card each day at the scheduled time</p>
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
						<label class="text-laya-secondary font-medium text-surface-300" for="briefing-time">
							Time
						</label>
						<input
							id="briefing-time"
							type="time"
							bind:value={time}
							oninput={() => debouncedSaveBriefing()}
							disabled={!enabled}
							class="h-9 w-36 rounded-md border border-surface-600 bg-surface-700 px-3 text-laya-base text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
						/>
					</div>

					<div class="flex flex-col gap-1">
						<label class="text-laya-secondary font-medium text-surface-300" for="briefing-tz">
							Timezone
						</label>
						<select
							id="briefing-tz"
							bind:value={timezone}
							onchange={() => debouncedSaveBriefing()}
							disabled={!enabled}
							class="h-9 rounded-md border border-surface-600 bg-surface-700 px-3 text-laya-base text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
						>
							{#each timezones as tz}
								<option value={tz}>{formatTzLabel(tz)}</option>
							{/each}
						</select>
					</div>
				</div>

				<!-- Per-space briefings toggle (only shown when multiple spaces exist) -->
				{#if spaceCount > 1}
					<div class="flex items-center justify-between rounded-md border {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-600 bg-surface-700/40'} px-4 py-3" class:opacity-40={!enabled}>
						<div>
							<span class="text-laya-base font-medium text-surface-100">Per-space briefings</span>
							<p class="text-laya-secondary text-surface-400">Generate a separate briefing for each space instead of one combined briefing</p>
						</div>
						<button
							class="relative h-6 w-11 shrink-0 rounded-full transition-colors {perSpace ? 'bg-laya-orange' : 'bg-surface-600'}"
							onclick={handlePerSpaceToggle}
							disabled={!enabled}
							role="switch"
							aria-checked={perSpace}
							aria-label="Toggle per-space briefings"
						>
							<span class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white transition-transform {perSpace ? 'translate-x-5' : ''}"></span>
						</button>
					</div>
				{/if}

				{#if error}
					<p class="text-laya-secondary text-red-400">{error}</p>
				{/if}

				{#if enabled}
					<p class="text-laya-secondary text-surface-500">
						{#if perSpace && spaceCount > 1}
							Each space will receive its own briefing daily at
						{:else}
							Briefing will run daily at
						{/if}
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
		<div class="mb-1 flex items-center justify-between">
			<div class="flex items-center gap-2">
				<h3 class="text-laya-heading font-medium">Context Association</h3>
				<span class="rounded-full border border-laya-orange/30 bg-laya-orange/10 px-2 py-0.5 text-laya-micro font-semibold uppercase tracking-wider text-laya-orange">Beta</span>
			</div>
			{#if contextSaving}
				<span class="text-laya-micro text-laya-orange">Saving…</span>
			{:else if contextSaved}
				<span class="text-laya-micro text-green-400">Saved</span>
			{/if}
		</div>
		<p class="mb-4 text-laya-base text-surface-400">
			Automatically detect when different notifications are about the same real-world context.
			For example, a bill notification and its payment receipt will be linked together.
			Works across different senders, threads, and platforms.
		</p>

		{#if !loading}
			<div class="space-y-4">
				<!-- Context Association main toggle -->
				<div class="rounded-md border {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-600 bg-surface-700/40'}">
					<div class="flex items-center justify-between px-4 py-3">
						<div>
							<span class="text-laya-base font-medium text-surface-100">Enable context association</span>
							<p class="text-laya-secondary text-surface-400">Compute semantic links between related cards during event processing</p>
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
							<p class="text-laya-secondary text-surface-500 flex items-center gap-1.5">
								Related cards detection is disabled.
							</p>
						</div>
					{/if}
				</div>

				<!-- Strictness presets (shown when enabled) -->
				{#if contextAssociationEnabled}
					<div class="rounded-md border {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-600 bg-surface-700/40'} px-4 py-3">
						<div class="flex items-center gap-2 mb-2">
							<span class="text-laya-base font-medium text-surface-100">Matching strictness</span>
						</div>

						<!-- Preset buttons -->
						<div class="flex gap-1 rounded-lg border {$glassTheme ? 'border-white/[0.06] bg-white/[0.02]' : 'border-surface-600 bg-surface-800'} p-1">
							{#each ['strict', 'balanced', 'lenient'] as preset}
								<button
									class="flex-1 rounded-md px-3 py-1.5 text-laya-secondary font-medium transition-colors {contextStrictness === preset ? 'bg-laya-orange/15 text-laya-orange' : $glassTheme ? 'text-surface-400 hover:text-surface-200 hover:bg-white/[0.08]' : 'text-surface-400 hover:text-surface-200 hover:bg-surface-700/50'}"
									onclick={() => selectContextPreset(preset as 'strict' | 'balanced' | 'lenient')}
								>
									{preset.charAt(0).toUpperCase() + preset.slice(1)}
								</button>
							{/each}
							{#if contextStrictness === 'custom'}
								<button
									class="flex-1 rounded-md px-3 py-1.5 text-laya-secondary font-medium bg-laya-orange/15 text-laya-orange"
									disabled
								>
									Custom
								</button>
							{/if}
						</div>

						<!-- Preset description with info tooltip (portal-based) -->
						<div class="mt-2 flex items-center gap-1.5">
							<svg
								class="h-3.5 w-3.5 shrink-0 text-surface-600 transition-colors hover:text-laya-orange cursor-help"
								fill="none" stroke="currentColor" viewBox="0 0 24 24"
								onmouseenter={showStrictnessTooltip}
								onmouseleave={hideStrictnessTooltip}
							>
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01" />
								<circle cx="12" cy="12" r="10" stroke-width="2" />
							</svg>
							<p class="text-laya-micro text-surface-500">
								{#if contextStrictness === 'strict'}
									Same issue, different platforms — requires shared identifiers
								{:else if contextStrictness === 'balanced'}
									Same context or topic — works across and within platforms
								{:else if contextStrictness === 'lenient'}
									Related notifications — broad matching for discovery
								{:else}
									Custom configuration active
								{/if}
							</p>
						</div>
					</div>

					<!-- Advanced settings (collapsible) -->
					<div class="rounded-md border {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-600 bg-surface-700/40'}">
						<button
							class="flex w-full items-center justify-between px-4 py-3"
							onclick={() => contextShowAdvanced = !contextShowAdvanced}
						>
							<span class="text-laya-secondary font-medium text-surface-300">Advanced settings</span>
							<svg class="h-4 w-4 text-surface-500 transition-transform {contextShowAdvanced ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
							</svg>
						</button>

						{#if contextShowAdvanced}
							<div class="border-t px-4 py-3 space-y-4 {$glassTheme ? 'border-white/[0.08]' : 'border-surface-600/50'}">
								<!-- Warning -->
								<div class="flex items-center gap-2 rounded-md border px-3 py-2 {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-600/50 bg-surface-800/50'}">
									<svg class="h-4 w-4 shrink-0 text-surface-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
									</svg>
									<p class="text-laya-micro text-surface-500">Changing these values overrides the preset above. Proceed with care — incorrect thresholds may produce too many or too few associations.</p>
								</div>

								<!-- Confidence threshold -->
								<div>
									<label for="ctx-confidence" class="text-laya-secondary text-surface-300">Confidence threshold</label>
									<p class="text-laya-micro text-surface-500 mb-1">Maximum cosine distance for candidates (lower = stricter)</p>
									<input
										id="ctx-confidence"
										type="number"
										step="0.01"
										min="0.05"
										max="0.60"
										bind:value={contextConfidence}
										onchange={handleAdvancedChange}
										class="w-24 rounded-md border border-surface-600 bg-surface-800 px-2 py-1 text-laya-secondary text-surface-200"
									/>
								</div>

								<!-- Auto-confirm threshold -->
								<div>
									<label for="ctx-autoconfirm" class="text-laya-secondary text-surface-300">Auto-confirm threshold</label>
									<p class="text-laya-micro text-surface-500 mb-1">Distance below which cards are linked without LLM review (leave empty to always require LLM)</p>
									<input
										id="ctx-autoconfirm"
										type="number"
										step="0.01"
										min="0.01"
										max="0.30"
										value={contextAutoConfirm ?? ''}
										onchange={(e) => { const v = (e.target as HTMLInputElement).value; contextAutoConfirm = v ? parseFloat(v) : null; handleAdvancedChange(); }}
										class="w-24 rounded-md border border-surface-600 bg-surface-800 px-2 py-1 text-laya-secondary text-surface-200"
										placeholder="None"
									/>
								</div>

								<!-- Centroid threshold -->
								<div>
									<label for="ctx-centroid" class="text-laya-secondary text-surface-300">Centroid threshold</label>
									<p class="text-laya-micro text-surface-500 mb-1">Maximum distance from group center for new members</p>
									<input
										id="ctx-centroid"
										type="number"
										step="0.01"
										min="0.05"
										max="0.60"
										bind:value={contextCentroid}
										onchange={handleAdvancedChange}
										class="w-24 rounded-md border border-surface-600 bg-surface-800 px-2 py-1 text-laya-secondary text-surface-200"
									/>
								</div>

								<!-- Cross-platform required -->
								<div class="flex items-center justify-between">
									<div>
										<span class="text-laya-secondary text-surface-300">Cross-platform required</span>
										<p class="text-laya-micro text-surface-500">Only link cards from different platforms</p>
									</div>
									<button
										class="relative h-5 w-9 shrink-0 rounded-full transition-colors {contextCrossPlatformRequired ? 'bg-laya-orange' : 'bg-surface-600'}"
										onclick={() => { contextCrossPlatformRequired = !contextCrossPlatformRequired; handleAdvancedChange(); }}
										role="switch"
										aria-checked={contextCrossPlatformRequired}
									>
										<span class="absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white transition-transform {contextCrossPlatformRequired ? 'translate-x-4' : ''}"></span>
									</button>
								</div>

								<!-- Entity ref overlap -->
								<div>
									<label for="ctx-overlap" class="text-laya-secondary text-surface-300">Entity ref overlap</label>
									<p class="text-laya-micro text-surface-500 mb-1">Whether shared identifiers are required for linking</p>
									<Dropdown
										id="ctx-overlap"
										bind:value={contextEntityRefOverlap}
										options={[
											{ value: 'hard_gate', label: 'Required (hard gate)' },
											{ value: 'soft_boost', label: 'Bonus (soft boost)' },
											{ value: 'disabled', label: 'Disabled' }
										]}
										onchange={() => handleAdvancedChange()}
										size="sm"
										compact
										class="w-56"
									/>
								</div>

								<!-- Always LLM -->
								<div class="flex items-center justify-between">
									<div>
										<span class="text-laya-secondary text-surface-300">Always use LLM</span>
										<p class="text-laya-micro text-surface-500">Require LLM confirmation for every match</p>
									</div>
									<button
										class="relative h-5 w-9 shrink-0 rounded-full transition-colors {contextAlwaysLlm ? 'bg-laya-orange' : 'bg-surface-600'}"
										onclick={() => { contextAlwaysLlm = !contextAlwaysLlm; handleAdvancedChange(); }}
										role="switch"
										aria-checked={contextAlwaysLlm}
									>
										<span class="absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white transition-transform {contextAlwaysLlm ? 'translate-x-4' : ''}"></span>
									</button>
								</div>
							</div>
						{/if}
					</div>
				{/if}

				{#if contextError}
					<p class="text-laya-secondary text-red-400">{contextError}</p>
				{/if}
			</div>
		{/if}
	</div>

	<!-- Group Summaries -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<div class="mb-1 flex items-center justify-between">
			<h3 class="text-laya-heading font-medium">Group Summaries</h3>
			{#if groupSumSaving}
				<span class="text-laya-micro text-laya-orange">Saving…</span>
			{:else if groupSumSaved}
				<span class="text-laya-micro text-green-400">Saved</span>
			{/if}
		</div>
		<p class="mb-4 text-laya-base text-surface-400">
			Generate rolling AI summaries for card groups. When multiple cards share the same entity,
			Laya synthesizes them into an executive snapshot that updates as new events arrive.
		</p>

		{#if !loading}
			<div class="space-y-4">
				<div class="flex items-center justify-between rounded-md border {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-600 bg-surface-700/40'} px-4 py-3">
					<div>
						<span class="text-laya-base font-medium text-surface-100">Enable group summaries</span>
						<p class="text-laya-secondary text-surface-400">Automatically summarize multi-card entity groups</p>
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
					<p class="text-laya-secondary text-red-400">{groupSumError}</p>
				{/if}
			</div>
		{/if}
	</div>

	<!-- Omni settings -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-lg border border-surface-700 bg-surface-800'} p-5">
		<div class="mb-1 flex items-center justify-between">
			<h3 class="text-laya-heading font-medium">Omni</h3>
			{#if omniSaving}
				<span class="text-laya-micro text-laya-orange">Saving…</span>
			{:else if omniSaved}
				<span class="text-laya-micro text-green-400">Saved</span>
			{/if}
		</div>
		<p class="mb-4 text-laya-base text-surface-400">
			Omni maintains a rolling cross-platform summary of your professional activity.
			Configure when resynthesis runs and how detailed the summary should be.
		</p>

		{#if !loading}
			<div class="space-y-4">
				<!-- Enabled toggle -->
				<div class="flex items-center justify-between rounded-md border {$glassTheme ? 'border-white/[0.08] bg-white/[0.04]' : 'border-surface-600 bg-surface-700/40'} px-4 py-3">
					<div>
						<span class="text-laya-base font-medium text-surface-100">Enable Omni</span>
						<p class="text-laya-secondary text-surface-400">Track and summarise activity across all platforms</p>
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
						<span class="text-laya-secondary font-medium text-surface-300">Summary density</span>
						<div class="flex rounded-lg border border-surface-600 overflow-hidden w-fit">
							{#each ['compact', 'standard', 'detailed'] as opt}
								<button
									class="px-3 py-1.5 text-laya-base font-medium transition-colors
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
						<p class="text-laya-secondary text-surface-500">{densityDescriptions[omniDensity]}</p>
					</div>

					<!-- Resynthesis time and timezone -->
					<div class="grid grid-cols-[auto_1fr] items-end gap-3">
						<div class="flex flex-col gap-1">
							<label class="text-laya-secondary font-medium text-surface-300" for="omni-time">
								Resynthesis time
							</label>
							<input
								id="omni-time"
								type="time"
								bind:value={omniResynthesisTime}
								oninput={() => debouncedSaveOmni()}
								disabled={!omniEnabled}
								class="h-9 w-36 rounded-md border border-surface-600 bg-surface-700 px-3 text-laya-base text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
							/>
						</div>

						<div class="flex flex-col gap-1">
							<label class="text-laya-secondary font-medium text-surface-300" for="omni-tz">
								Timezone
							</label>
							<select
								id="omni-tz"
								bind:value={omniTimezone}
								onchange={() => debouncedSaveOmni()}
								disabled={!omniEnabled}
								class="h-9 rounded-md border border-surface-600 bg-surface-700 px-3 text-laya-base text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
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
							<label class="text-laya-secondary font-medium text-surface-300" for="omni-rolling">
								Rolling interval
							</label>
							<select
								id="omni-rolling"
								bind:value={omniRollingHours}
								onchange={() => debouncedSaveOmni()}
								disabled={!omniEnabled}
								class="h-9 w-36 rounded-md border border-surface-600 bg-surface-700 px-3 text-laya-base text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
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
							<label class="text-laya-secondary font-medium text-surface-300" for="omni-threshold">
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
								class="h-9 w-36 rounded-md border border-surface-600 bg-surface-700 px-3 text-laya-base text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
								placeholder="0 = off"
							/>
						</div>
					</div>

					<p class="text-laya-secondary text-surface-500">
						Resynthesis triggers: daily at <span class="text-surface-300">{omniPreviewTime}</span>{omniRollingHours > 0 ? `, every ${omniRollingHours}h` : ''}{omniEventThreshold > 0 ? `, or after ${omniEventThreshold} new events` : ''} — whichever comes first.
						Synthesis is skipped automatically if no new cards have arrived since the last run.
					</p>

					{#if omniError}
						<p class="text-laya-secondary text-red-400">{omniError}</p>
					{/if}
				</div>
			</div>
		{/if}
	</div>
</div>

{#if strictnessTooltip}
	<span
		use:portal
		class="pointer-events-none fixed z-[100] max-w-xs rounded-md border border-transparent glass-tooltip px-2.5 py-2 text-laya-micro leading-relaxed whitespace-normal"
		style="top: {strictnessTooltip.top}px; left: {strictnessTooltip.left}px;"
	>
		{strictnessTooltip.text}
	</span>
{/if}
