<script lang="ts">
	import { theme, type Theme } from '$lib/stores/theme';
	import { cardColors } from '$lib/stores/cardColors';
	import { accessibleColors } from '$lib/stores/accessibleColors';
	import { reducedMotion } from '$lib/stores/reducedMotion';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { cardDescriptions } from '$lib/stores/cardDescriptions';
	import { cardSize } from '$lib/stores/cardSize';
	import { fontScale, type FontScale } from '$lib/stores/fontScale';

	const fontSteps: FontScale[] = [12, 13, 14, 15];
	const fontLabels: Record<FontScale, string> = { 12: 'Compact', 13: 'Default', 14: 'Relaxed', 15: 'Large' };
	let stepIndex = $derived(fontSteps.indexOf($fontScale));

	// Mockup color hues — shift when accessible mode is on
	const hDone = $derived($accessibleColors ? 195 : 162);    // emerald → teal
	const hApproval = $derived($accessibleColors ? 240 : 285); // violet → blue
	const hFailed = $derived($accessibleColors ? 40 : 25);     // rose → warm vermillion
</script>

<div class="space-y-8">

	<!-- Theme toggle -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-6">
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
							<div class="h-10 rounded-md" style="border:1px solid {$cardColors ? `oklch(0.51 0.14 ${hDone} / 20%)` : 'oklch(0.34 0.009 52)'}; background:{$cardColors ? `oklch(0.21 0.04 ${hDone} / 50%)` : 'oklch(0.265 0.008 50)'}"></div>
						</div>
						<div class="flex flex-1 flex-col gap-1">
							<div class="h-14 rounded-md" style="border:1px solid {$cardColors ? `oklch(0.51 0.1 ${hApproval} / 25%)` : 'oklch(0.34 0.009 52)'}; background:{$cardColors ? `oklch(0.21 0.06 ${hApproval} / 55%)` : 'oklch(0.265 0.008 50)'}"></div>
							<div class="h-6 rounded-md" style="border:1px solid {$cardColors ? `oklch(0.51 0.1 ${hFailed} / 35%)` : 'oklch(0.34 0.009 52)'}; background:{$cardColors ? `oklch(0.21 0.05 ${hFailed} / 60%)` : 'oklch(0.265 0.008 50)'}"></div>
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
							<div class="h-10 rounded-md" style="border:1px solid {$cardColors ? `oklch(0.82 0.04 ${hDone} / 30%)` : 'oklch(0.88 0.006 70)'}; background:{$cardColors ? `oklch(0.97 0.015 ${hDone})` : 'oklch(0.935 0.006 72)'}"></div>
						</div>
						<div class="flex flex-1 flex-col gap-1">
							<div class="h-14 rounded-md" style="border:1px solid {$cardColors ? `oklch(0.74 0.07 ${hApproval} / 45%)` : 'oklch(0.88 0.006 70)'}; background:{$cardColors ? `oklch(0.94 0.04 ${hApproval})` : 'oklch(0.935 0.006 72)'}"></div>
							<div class="h-6 rounded-md" style="border:1px solid {$cardColors ? `oklch(0.74 0.08 ${hFailed} / 55%)` : 'oklch(0.88 0.006 70)'}; background:{$cardColors ? `oklch(0.94 0.045 ${hFailed})` : 'oklch(0.935 0.006 72)'}"></div>
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

	<!-- Glass Theme toggle -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-6">
		<div class="flex items-center justify-between">
			<div>
				<h3 class="mb-1 font-semibold text-surface-50">Glass Theme</h3>
				<p class="text-sm text-surface-400">Frosted glass effect on cards and list rows. Adds backdrop blur and translucent surfaces.</p>
			</div>
			<button
				class="relative h-6 w-11 shrink-0 rounded-full transition-colors {$glassTheme ? 'bg-laya-orange' : 'bg-surface-600'}"
				onclick={() => glassTheme.set(!$glassTheme)}
				role="switch"
				aria-checked={$glassTheme}
				aria-label="Toggle glass theme"
			>
				<span
					class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform {$glassTheme ? 'translate-x-5' : 'translate-x-0'}"
				></span>
			</button>
		</div>
	</div>

	<!-- Status Colors — parent toggle with Accessible Colors as a nested sub-setting.
	     The sub-setting is dimmed/disabled when the parent is off, since accessible
	     colors only shift the status palette and has no effect without it. -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-6">
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

		<!-- Accessible Colors sub-setting -->
		<div class="mt-5 border-t border-surface-700/60 pt-5 pl-4 {$cardColors ? '' : 'opacity-50'}">
			<div class="flex items-center justify-between">
				<div>
					<h4 class="mb-0.5 text-sm font-semibold text-surface-100">Accessible Colors</h4>
					<p class="text-xs text-surface-400">Colorblind-friendly palette. Shifts status colors for better contrast across all vision types.</p>
				</div>
				<button
					class="relative h-6 w-11 shrink-0 rounded-full transition-colors {$accessibleColors && $cardColors ? 'bg-laya-orange' : 'bg-surface-600'} disabled:cursor-not-allowed"
					onclick={() => accessibleColors.set(!$accessibleColors)}
					disabled={!$cardColors}
					title={$cardColors ? '' : 'Enable Status Colors to use this setting'}
					role="switch"
					aria-checked={$accessibleColors}
					aria-label="Toggle accessible colors"
				>
					<span
						class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform {$accessibleColors ? 'translate-x-5' : 'translate-x-0'}"
					></span>
				</button>
			</div>

			<!-- Color legend showing the accessible palette (only when actually active) -->
			{#if $accessibleColors && $cardColors}
				<div class="mt-3 flex flex-wrap gap-3 text-[11px] text-surface-400">
					<div class="flex items-center gap-1.5">
						<span class="h-2.5 w-2.5 rounded-full" style="background: oklch(0.75 0.17 65)"></span>
						Pending
					</div>
					<div class="flex items-center gap-1.5">
						<span class="h-2.5 w-2.5 rounded-full" style="background: oklch(0.68 0.16 240)"></span>
						Approval
					</div>
					<div class="flex items-center gap-1.5">
						<span class="h-2.5 w-2.5 rounded-full" style="background: oklch(0.72 0.12 195)"></span>
						Done
					</div>
					<div class="flex items-center gap-1.5">
						<span class="h-2.5 w-2.5 rounded-full" style="background: oklch(0.62 0.18 40)"></span>
						Failed
					</div>
				</div>
			{/if}
		</div>
	</div>

	<!-- Reduce motion toggle — grouped with Status Colors as accessibility settings -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-6">
		<div class="flex items-center justify-between">
			<div>
				<h3 class="mb-1 font-semibold text-surface-50">Reduce Motion</h3>
				<p class="text-sm text-surface-400">Disable tab transitions, panel slides, and card reflow animations. Recommended if motion causes discomfort.</p>
			</div>
			<button
				class="relative h-6 w-11 shrink-0 rounded-full transition-colors {$reducedMotion ? 'bg-laya-orange' : 'bg-surface-600'}"
				onclick={() => reducedMotion.set(!$reducedMotion)}
				role="switch"
				aria-checked={$reducedMotion}
				aria-label="Toggle reduced motion"
			>
				<span
					class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform {$reducedMotion ? 'translate-x-5' : 'translate-x-0'}"
				></span>
			</button>
		</div>
	</div>

	<!-- Show Card Descriptions toggle -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-6">
		<div class="flex items-center justify-between">
			<div>
				<h3 class="mb-1 font-semibold text-surface-50">Show Card Descriptions</h3>
				<p class="text-sm text-surface-400">Show summary text on cards in the feed. Turning this off makes cards more compact.</p>
			</div>
			<button
				class="relative h-6 w-11 shrink-0 rounded-full transition-colors {$cardDescriptions ? 'bg-laya-orange' : 'bg-surface-600'}"
				onclick={() => cardDescriptions.set(!$cardDescriptions)}
				role="switch"
				aria-checked={$cardDescriptions}
				aria-label="Toggle card descriptions"
			>
				<span
					class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform {$cardDescriptions ? 'translate-x-5' : 'translate-x-0'}"
				></span>
			</button>
		</div>
	</div>

	<!-- Card size segmented control — controls vertical density of feed cards -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-6">
		<div class="flex items-center justify-between gap-6">
			<div>
				<h3 class="mb-1 font-semibold text-surface-50">Card Size</h3>
				<p class="text-sm text-surface-400">Compact stacks more cards per screen by inlining metadata and tightening spacing. Relaxed shows the full layout.</p>
			</div>
			<div role="radiogroup" aria-label="Card size" class="inline-flex shrink-0 rounded-lg border border-surface-700 bg-surface-900/50 p-0.5">
				<button
					role="radio"
					aria-checked={$cardSize === 'compact'}
					class="rounded-md px-3 py-1 text-xs font-medium transition-colors {$cardSize === 'compact' ? 'bg-laya-orange text-white' : 'text-surface-400 hover:text-surface-200'}"
					onclick={() => cardSize.set('compact')}
				>Compact</button>
				<button
					role="radio"
					aria-checked={$cardSize === 'relaxed'}
					class="rounded-md px-3 py-1 text-xs font-medium transition-colors {$cardSize === 'relaxed' ? 'bg-laya-orange text-white' : 'text-surface-400 hover:text-surface-200'}"
					onclick={() => cardSize.set('relaxed')}
				>Relaxed</button>
			</div>
		</div>
	</div>

	<!-- Font scale -->
	<div class="{$glassTheme ? 'glass-section' : 'rounded-xl border border-surface-700 bg-surface-800'} p-6">
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
