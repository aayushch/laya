<script lang="ts">
	import { theme, type Theme } from '$lib/stores/theme';
	import { cardColors } from '$lib/stores/cardColors';
	import { fontScale, type FontScale } from '$lib/stores/fontScale';

	const fontSteps: FontScale[] = [12, 13, 14, 15];
	const fontLabels: Record<FontScale, string> = { 12: 'Compact', 13: 'Default', 14: 'Relaxed', 15: 'Large' };
	let stepIndex = $derived(fontSteps.indexOf($fontScale));
</script>

<div class="space-y-8">

	<!-- Theme toggle -->
	<div class="rounded-xl border border-surface-700 bg-surface-800 p-6">
		<h3 class="mb-1 font-semibold text-surface-50">Appearance</h3>
		<p class="mb-5 text-sm text-surface-400">Choose between dark and light interface themes.</p>

		<div class="flex gap-3">
			<!-- Dark -->
			<button
				class="group relative flex flex-1 flex-col items-center gap-3 rounded-xl border-2 p-4 transition-all
					{$theme === 'dark'
						? 'border-laya-orange bg-surface-700'
						: 'border-surface-600 bg-surface-900 hover:border-surface-500'}"
				onclick={() => theme.set('dark')}
			>
				<!-- Mini mockup - dark -->
				<div class="w-full overflow-hidden rounded-lg border border-surface-600 bg-[oklch(0.185_0.007_48)]">
					<div class="flex items-center gap-1.5 border-b border-[oklch(0.34_0.009_52)] px-3 py-2">
						<div class="h-2 w-8 rounded-full bg-laya-orange/80"></div>
						<div class="h-1.5 w-5 rounded-full bg-[oklch(0.42_0.011_54)]"></div>
						<div class="h-1.5 w-5 rounded-full bg-[oklch(0.42_0.011_54)]"></div>
					</div>
					<div class="flex gap-1.5 p-2">
						<div class="flex flex-1 flex-col gap-1">
							<div class="h-10 rounded-md" style="border:1px solid {$cardColors ? 'oklch(0.51 0.077 68 / 30%)' : 'oklch(0.34 0.009 52)'}; background:{$cardColors ? 'oklch(0.21 0.039 68 / 55%)' : 'oklch(0.265 0.008 50)'}"></div>
							<div class="h-10 rounded-md" style="border:1px solid {$cardColors ? 'oklch(0.51 0.14 160 / 20%)' : 'oklch(0.34 0.009 52)'}; background:{$cardColors ? 'oklch(0.21 0.04 162 / 50%)' : 'oklch(0.265 0.008 50)'}"></div>
						</div>
						<div class="flex flex-1 flex-col gap-1">
							<div class="h-14 rounded-md" style="border:1px solid {$cardColors ? 'oklch(0.51 0.1 285 / 25%)' : 'oklch(0.34 0.009 52)'}; background:{$cardColors ? 'oklch(0.21 0.06 285 / 55%)' : 'oklch(0.265 0.008 50)'}"></div>
							<div class="h-6 rounded-md" style="border:1px solid {$cardColors ? 'oklch(0.51 0.1 25 / 35%)' : 'oklch(0.34 0.009 52)'}; background:{$cardColors ? 'oklch(0.21 0.05 25 / 60%)' : 'oklch(0.265 0.008 50)'}"></div>
						</div>
					</div>
				</div>

				<span class="text-sm font-medium text-surface-200">Dark</span>

				{#if $theme === 'dark'}
					<div class="absolute right-3 top-3 flex h-5 w-5 items-center justify-center rounded-full bg-laya-orange text-[10px] text-white">✓</div>
				{/if}
			</button>

			<!-- Light -->
			<button
				class="group relative flex flex-1 flex-col items-center gap-3 rounded-xl border-2 p-4 transition-all
					{$theme === 'light'
						? 'border-laya-orange bg-surface-700'
						: 'border-surface-600 bg-surface-900 hover:border-surface-500'}"
				onclick={() => theme.set('light')}
			>
				<!-- Mini mockup - light -->
				<div class="w-full overflow-hidden rounded-lg border border-[oklch(0.88_0.006_70)] bg-[oklch(0.970_0.005_74)]">
					<div class="flex items-center gap-1.5 border-b border-[oklch(0.88_0.006_70)] px-3 py-2 bg-[oklch(0.97_0.005_74)]">
						<div class="h-2 w-8 rounded-full bg-laya-orange"></div>
						<div class="h-1.5 w-5 rounded-full bg-[oklch(0.81_0.007_68)]"></div>
						<div class="h-1.5 w-5 rounded-full bg-[oklch(0.81_0.007_68)]"></div>
					</div>
					<div class="flex gap-1.5 p-2">
						<div class="flex flex-1 flex-col gap-1">
							<div class="h-10 rounded-md" style="border:1px solid {$cardColors ? 'oklch(0.80 0.07 70 / 55%)' : 'oklch(0.88 0.006 70)'}; background:{$cardColors ? 'oklch(0.94 0.045 75)' : 'oklch(0.935 0.006 72)'}"></div>
							<div class="h-10 rounded-md" style="border:1px solid {$cardColors ? 'oklch(0.82 0.04 162 / 30%)' : 'oklch(0.88 0.006 70)'}; background:{$cardColors ? 'oklch(0.97 0.015 162)' : 'oklch(0.935 0.006 72)'}"></div>
						</div>
						<div class="flex flex-1 flex-col gap-1">
							<div class="h-14 rounded-md" style="border:1px solid {$cardColors ? 'oklch(0.74 0.07 285 / 45%)' : 'oklch(0.88 0.006 70)'}; background:{$cardColors ? 'oklch(0.94 0.04 285)' : 'oklch(0.935 0.006 72)'}"></div>
							<div class="h-6 rounded-md" style="border:1px solid {$cardColors ? 'oklch(0.74 0.08 25 / 55%)' : 'oklch(0.88 0.006 70)'}; background:{$cardColors ? 'oklch(0.94 0.045 25)' : 'oklch(0.935 0.006 72)'}"></div>
						</div>
					</div>
				</div>

				<span class="text-sm font-medium text-surface-200">Light</span>

				{#if $theme === 'light'}
					<div class="absolute right-3 top-3 flex h-5 w-5 items-center justify-center rounded-full bg-laya-orange text-[10px] text-white">✓</div>
				{/if}
			</button>
		</div>
	</div>

	<!-- Card colors toggle -->
	<div class="rounded-xl border border-surface-700 bg-surface-800 p-6">
		<div class="flex items-center justify-between">
			<div>
				<h3 class="mb-1 font-semibold text-surface-50">Status Colors</h3>
				<p class="text-sm text-surface-400">Tint cards and list rows by their status. Turn off for a uniform look.</p>
			</div>
			<button
				class="relative h-6 w-11 shrink-0 rounded-full transition-colors {$cardColors ? 'bg-laya-orange' : 'bg-surface-600'}"
				onclick={() => cardColors.set(!$cardColors)}
				role="switch"
				aria-checked={$cardColors}
				aria-label="Toggle status colors"
			>
				<span
					class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform {$cardColors ? 'translate-x-5' : 'translate-x-0'}"
				></span>
			</button>
		</div>
	</div>

	<!-- Font scale -->
	<div class="rounded-xl border border-surface-700 bg-surface-800 p-6">
		<h3 class="mb-1 font-semibold text-surface-50">Text Size</h3>
		<p class="mb-5 text-sm text-surface-400">Adjust the base font size for chat messages and card content.</p>

		<div class="space-y-3">
			<!-- Step buttons -->
			<div class="flex gap-2">
				{#each fontSteps as step, i}
					<button
						class="flex-1 rounded-lg border-2 px-3 py-2 text-center transition-all
							{$fontScale === step
								? 'border-laya-orange bg-laya-orange/10 text-surface-100'
								: 'border-surface-600 bg-surface-900 text-surface-400 hover:border-surface-500'}"
						onclick={() => fontScale.set(step)}
					>
						<span class="block text-xs font-medium">{fontLabels[step]}</span>
						<span class="block text-[10px] text-surface-500">{step}px</span>
					</button>
				{/each}
			</div>

			<!-- Preview -->
			<div class="rounded-lg border border-surface-700 bg-surface-900/50 px-4 py-3">
				<p class="text-surface-300" style="font-size: {$fontScale}px; line-height: 1.5;">
					The quick brown fox jumps over the lazy dog.
				</p>
			</div>
		</div>
	</div>

</div>
