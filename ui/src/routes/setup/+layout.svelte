<script lang="ts">
	import type { Snippet } from 'svelte';
	let { children }: { children: Snippet } = $props();
</script>

<div class="flex min-h-screen items-center justify-center bg-surface-900 p-4 pt-[38px]">
	<div class="w-full max-w-lg">
		{@render children()}
	</div>
</div>
