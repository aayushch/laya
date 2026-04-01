<script lang="ts">
	import { theme, type Theme } from '$lib/stores/theme';
	import { cardColors } from '$lib/stores/cardColors';
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
							<div class="h-10 rounded-md border {$cardColors ? 'border-amber-800/30 bg-amber-950/55' : 'border-[oklch(0.34_0.009_52)] bg-[oklch(0.265_0.008_50)]'}"></div>
							<div class="h-10 rounded-md border {$cardColors ? 'border-emerald-800/20 bg-emerald-950/50' : 'border-[oklch(0.34_0.009_52)] bg-[oklch(0.265_0.008_50)]'}"></div>
						</div>
						<div class="flex flex-1 flex-col gap-1">
							<div class="h-14 rounded-md border {$cardColors ? 'border-violet-800/25 bg-violet-950/55' : 'border-[oklch(0.34_0.009_52)] bg-[oklch(0.265_0.008_50)]'}"></div>
							<div class="h-6 rounded-md border {$cardColors ? 'border-rose-800/35 bg-rose-950/60' : 'border-[oklch(0.34_0.009_52)] bg-[oklch(0.265_0.008_50)]'}"></div>
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
							<div class="h-10 rounded-md border {$cardColors ? 'border-amber-800/30 bg-amber-950/55' : 'border-[oklch(0.88_0.006_70)] bg-[oklch(0.935_0.006_72)]'}"></div>
							<div class="h-10 rounded-md border {$cardColors ? 'border-emerald-800/20 bg-emerald-950/50' : 'border-[oklch(0.88_0.006_70)] bg-[oklch(0.935_0.006_72)]'}"></div>
						</div>
						<div class="flex flex-1 flex-col gap-1">
							<div class="h-14 rounded-md border {$cardColors ? 'border-violet-800/25 bg-violet-950/55' : 'border-[oklch(0.88_0.006_70)] bg-[oklch(0.935_0.006_72)]'}"></div>
							<div class="h-6 rounded-md border {$cardColors ? 'border-rose-800/35 bg-rose-950/60' : 'border-[oklch(0.88_0.006_70)] bg-[oklch(0.935_0.006_72)]'}"></div>
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

</div>
