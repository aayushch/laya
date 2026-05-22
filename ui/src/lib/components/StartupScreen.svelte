<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { health, healthError } from '$lib/stores/health';
	import { wsStatus } from '$lib/stores/websocket';
	import {
		needsSetup,
		setupSteps,
		setupError,
		setupComplete,
		checkEnvironment,
		runSetup,
		type SetupStep,
	} from '$lib/stores/setup';
	import { onMount } from 'svelte';
	import layaIcon from '$lib/assets/laya-splash.png';

	// ── Normal startup components (shown after setup is done) ──
	interface ComponentStatus {
		label: string;
		state: 'waiting' | 'loading' | 'ready' | 'error';
	}

	const components = $derived.by((): ComponentStatus[] => {
		const h = $health;
		const err = $healthError;

		const engineState: ComponentStatus['state'] = !h
			? (err ? 'loading' : 'waiting')
			: h.engine === 'healthy' ? 'ready' : 'error';

		const sqliteState: ComponentStatus['state'] = !h
			? 'waiting'
			: h.sqlite === 'healthy' ? 'ready' : (h.sqlite === 'unhealthy' ? 'error' : 'loading');

		const n8nState: ComponentStatus['state'] = !h
			? 'waiting'
			: h.n8n === 'healthy' ? 'ready' : (h.n8n === 'unhealthy' ? 'error' : 'loading');

		const wsState: ComponentStatus['state'] =
			$wsStatus === 'connected' ? 'ready'
			: $wsStatus === 'connecting' ? 'loading'
			: engineState === 'ready' ? 'loading' : 'waiting';

		return [
			{ label: 'Engine', state: engineState },
			{ label: 'Database', state: sqliteState },
			{ label: 'n8n Workflows', state: n8nState },
			{ label: 'WebSocket', state: wsState },
		];
	});

	const readyCount = $derived(components.filter(c => c.state === 'ready').length);
	const allReady = $derived(readyCount === components.length);

	// ── Setup phase ──
	let setupStarted = $state(false);

	const setupReadyCount = $derived(($setupSteps).filter(s => s.status === 'done').length);
	const setupTotal = $derived(($setupSteps).length);

	onMount(async () => {
		// Check environment (only works in Tauri, no-ops in dev browser)
		await checkEnvironment();

		// If setup is needed and no error (Python found), auto-start
		if ($needsSetup && !$setupError) {
			setupStarted = true;
			runSetup();
		}
	});

	function retrySetup() {
		setupStarted = true;
		runSetup();
	}

	const stateIcon: Record<string, string> = {
		waiting: 'text-surface-600',
		loading: 'text-laya-orange animate-pulse',
		running: 'text-laya-orange animate-pulse',
		ready: 'text-green-500',
		done: 'text-green-500',
		warning: 'text-yellow-500',
		error: 'text-red-500',
	};

	const stateLabel: Record<string, string> = {
		waiting: 'Waiting',
		loading: 'Starting',
		running: 'Running',
		ready: 'Ready',
		done: 'Done',
		warning: 'Warning',
		error: 'Error',
	};

	function dotColor(state: string): string {
		if (state === 'ready' || state === 'done') return 'bg-green-500';
		if (state === 'warning') return 'bg-yellow-500';
		if (state === 'error') return 'bg-red-500';
		if (state === 'loading' || state === 'running') return 'bg-laya-orange';
		return 'bg-surface-600';
	}
</script>

<div class="flex h-screen w-full flex-col items-center justify-center bg-surface-900 pt-[38px]">
	<!-- Logo -->
	<h1 class="mb-2 text-8xl font-bold tracking-wide text-laya-orange">Laya</h1>

	{#if $needsSetup === null}
		<!-- Still checking environment -->
		<p class="text-sm text-surface-500 animate-pulse">Checking environment...</p>

	{:else if $needsSetup && !$setupComplete}
		<!-- Setup phase -->
		<div class="w-80 space-y-3">
			{#if $setupError && !setupStarted}
				<!-- Fatal error (e.g., no Python) -->
				<div class="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-center">
					<p class="text-sm text-red-400 break-words">{$setupError}</p>
				</div>
			{:else}
				<!-- Setup steps -->
				{#each $setupSteps as step}
					<div class="rounded-lg border border-surface-700/50 bg-surface-800/50 px-4 py-2.5">
						<div class="flex items-center gap-3">
							<span class="relative flex h-2.5 w-2.5 shrink-0">
								{#if step.status === 'running'}
									<span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-laya-orange opacity-50"></span>
								{/if}
								<span class="relative inline-flex h-2.5 w-2.5 rounded-full {dotColor(step.status)}"></span>
							</span>
							<span class="flex-1 text-sm text-surface-200">{step.label}</span>
							<span class="text-xs {stateIcon[step.status]}">{stateLabel[step.status]}</span>
						</div>
						<p class="h-4 text-[10px] truncate mt-1 pl-[22px] {step.status === 'error' ? 'text-red-400' : step.status === 'running' ? 'text-surface-500' : 'text-transparent'}">{step.message || '\u00A0'}</p>
					</div>
				{/each}

				{#if $setupError}
					<div class="mt-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3">
						<p class="text-xs text-red-400 break-words">{$setupError}</p>
						<button
							class="mt-2 rounded-md bg-surface-700 px-3 py-1 text-xs text-surface-200 transition-colors hover:bg-surface-600"
							onclick={retrySetup}
						>
							Retry
						</button>
					</div>
				{/if}
			{/if}
		</div>

		<!-- Progress bar -->
		<div class="mt-6 h-1 w-80 overflow-hidden rounded-full bg-surface-700">
			<div
				class="h-full rounded-full bg-laya-orange transition-all duration-500"
				style="width: {(setupReadyCount / setupTotal) * 100}%"
			></div>
		</div>

		<p class="mt-4 text-xs text-surface-500">
			{#if $setupError && !setupStarted}
				Setup cannot continue
			{:else}
				Setting up Laya... {setupReadyCount}/{setupTotal} steps complete
			{/if}
		</p>

	{:else}
		<!-- Normal startup: icon + progress -->
		<img
			src={layaIcon}
			alt="Laya"
			class="w-56 h-56 object-contain {allReady ? '' : 'animate-pulse'}"
		/>

	{/if}
</div>
