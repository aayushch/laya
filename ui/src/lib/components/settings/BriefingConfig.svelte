<script lang="ts">
	import { engineApi } from '$lib/api/engine';

	let enabled = $state(true);
	let time = $state('07:00');
	let timezone = $state('America/New_York');
	let loading = $state(true);
	let saving = $state(false);
	let saved = $state(false);
	let error = $state('');

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
			loading = false;
		});
	});

	async function save() {
		saving = true;
		error = '';
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
						onclick={() => (enabled = !enabled)}
						role="switch"
						aria-checked={enabled}
					>
						<span class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white transition-transform {enabled ? 'translate-x-5' : ''}"></span>
					</button>
				</div>

				<!-- Time and timezone -->
				<div class="flex flex-wrap items-end gap-4" class:opacity-40={!enabled}>
					<div class="flex flex-col gap-1">
						<label class="text-xs font-medium text-surface-300" for="briefing-time">
							Time
						</label>
						<input
							id="briefing-time"
							type="time"
							bind:value={time}
							disabled={!enabled}
							class="w-36 rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
						/>
					</div>

					<div class="flex flex-col gap-1">
						<label class="text-xs font-medium text-surface-300" for="briefing-tz">
							Timezone
						</label>
						<select
							id="briefing-tz"
							bind:value={timezone}
							disabled={!enabled}
							class="w-72 rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-sm text-surface-50 focus:border-laya-orange/50 focus:outline-none disabled:cursor-not-allowed"
						>
							{#each timezones as tz}
								<option value={tz}>{formatTzLabel(tz)}</option>
							{/each}
						</select>
					</div>

					<button
						class="rounded-md bg-laya-orange/80 px-4 py-1.5 text-sm font-medium text-surface-900 transition-colors hover:bg-laya-orange disabled:opacity-50"
						onclick={save}
						disabled={saving || !enabled}
					>
						{saving ? 'Saving…' : saved ? 'Saved ✓' : 'Save'}
					</button>
				</div>

				{#if error}
					<p class="text-xs text-red-400">{error}</p>
				{/if}

				{#if enabled}
					<p class="text-xs text-surface-500">
						Briefing will run daily at
						<span class="text-surface-300">{previewTime}</span>
						in
						<span class="text-surface-300">{timezone.replace(/_/g, ' ')}</span>.
						Changes take effect on the next scheduled cycle (within 60 seconds).
					</p>
				{/if}
			</div>
		{/if}
	</div>
</div>
