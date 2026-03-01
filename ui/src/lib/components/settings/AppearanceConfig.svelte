<script lang="ts">
	import { theme, type Theme } from '$lib/stores/theme';

	const swatches = [
		{ color: '#f6ac69', label: 'Orange',     cls: 'bg-laya-orange' },
		{ color: '#f6bc66', label: 'Gold',        cls: 'bg-laya-gold' },
		{ color: '#ffdab9', label: 'Peach',       cls: 'bg-laya-peach' },
		{ color: '#ff9770', label: 'Coral',       cls: 'bg-laya-coral' },
		{ color: '#c97b42', label: 'Amber',       cls: 'bg-laya-amber' },
		{ color: '#e8d5b0', label: 'Sand',        cls: 'bg-laya-sand' },
		{ color: '#f2e0d5', label: 'Blush',       cls: 'bg-laya-blush' },
		{ color: '#9e5a3d', label: 'Terracotta',  cls: 'bg-laya-terracotta' }
	];
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
							<div class="h-10 rounded-md border border-[oklch(0.34_0.009_52)] bg-[oklch(0.265_0.008_50)]"></div>
							<div class="h-10 rounded-md border border-[oklch(0.34_0.009_52)] bg-[oklch(0.265_0.008_50)]"></div>
						</div>
						<div class="flex flex-1 flex-col gap-1">
							<div class="h-14 rounded-md border border-[oklch(0.34_0.009_52)] bg-[oklch(0.265_0.008_50)]"></div>
							<div class="h-6 rounded-md border border-[oklch(0.34_0.009_52)] bg-[oklch(0.265_0.008_50)]"></div>
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
							<div class="h-10 rounded-md border border-[oklch(0.88_0.006_70)] bg-[oklch(0.935_0.006_72)]"></div>
							<div class="h-10 rounded-md border border-[oklch(0.88_0.006_70)] bg-[oklch(0.935_0.006_72)]"></div>
						</div>
						<div class="flex flex-1 flex-col gap-1">
							<div class="h-14 rounded-md border border-[oklch(0.88_0.006_70)] bg-[oklch(0.935_0.006_72)]"></div>
							<div class="h-6 rounded-md border border-[oklch(0.88_0.006_70)] bg-[oklch(0.935_0.006_72)]"></div>
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

	<!-- Brand palette -->
	<div class="rounded-xl border border-surface-700 bg-surface-800 p-6">
		<h3 class="mb-1 font-semibold text-surface-50">Brand Palette</h3>
		<p class="mb-5 text-sm text-surface-400">Laya's warm pastel color system — used across the interface for accents, highlights, and interactive elements.</p>

		<div class="grid grid-cols-4 gap-3">
			{#each swatches as swatch}
				<div class="flex flex-col items-center gap-2">
					<div
						class="h-12 w-full rounded-lg shadow-sm {swatch.cls}"
						title={swatch.color}
					></div>
					<span class="text-[11px] font-medium text-surface-300">{swatch.label}</span>
					<span class="font-mono text-[10px] text-surface-500">{swatch.color}</span>
				</div>
			{/each}
		</div>
	</div>

	<!-- Quick tip -->
	<div class="flex items-start gap-3 rounded-xl border border-laya-orange/20 bg-laya-orange/5 px-5 py-4">
		<div class="mt-0.5 h-4 w-4 shrink-0 rounded-full bg-laya-orange/30 text-center text-[10px] leading-4 text-laya-orange font-bold">i</div>
		<p class="text-sm text-surface-300">
			Theme preference is saved locally and persists across sessions. Laya's warm palette is optimised for both dark and light environments.
		</p>
	</div>

</div>
