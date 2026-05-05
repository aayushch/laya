<script lang="ts">
	import { compose } from '$lib/stores/compose';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { tick } from 'svelte';
	import type { EgressConnection, ComposePlatform, ComposeAction, ComposeField } from '$lib/api/types';

	let connections = $state<EgressConnection[]>([]);
	let connectionsLoaded = $state(false);
	let selectedConnectionId = $state<string>('');

	// Registry-driven platform/action data
	let allPlatforms = $state<ComposePlatform[]>([]);
	let platformsLoaded = $state(false);

	// Form state
	let sending = $state(false);
	let aiAssisting = $state(false);
	let success = $state(false);
	let resultUrl = $state<string | null>(null);
	let error = $state<string | null>(null);

	// Generic form values keyed by field name
	let formValues: Record<string, string> = $state({});

	let activePlatform = $state('');
	let selectedActionType = $state('');
	let initialSyncDone = $state(false);

	// Email autocomplete
	let emailSuggestions = $state<string[]>([]);
	let emailDropdownField = $state<string | null>(null);
	let emailDebounceTimer = $state<ReturnType<typeof setTimeout> | null>(null);
	let emailHighlightIndex = $state(-1);
	let emailListEl = $state<HTMLDivElement | undefined>();

	function handleEmailInput(fieldName: string, value: string) {
		formValues[fieldName] = value;
		if (emailDebounceTimer) clearTimeout(emailDebounceTimer);
		if (value.trim().length < 2) {
			emailSuggestions = [];
			emailDropdownField = null;
			return;
		}
		emailDebounceTimer = setTimeout(async () => {
			try {
				const resp = await engineApi.emailSuggestions(value.trim());
				emailSuggestions = resp.suggestions;
				emailDropdownField = resp.suggestions.length > 0 ? fieldName : null;
				emailHighlightIndex = -1;
			} catch {
				emailSuggestions = [];
				emailDropdownField = null;
			}
		}, 250);
	}

	function selectEmailSuggestion(fieldName: string, email: string) {
		formValues[fieldName] = email;
		emailSuggestions = [];
		emailDropdownField = null;
		emailHighlightIndex = -1;
	}

	async function scrollHighlightedIntoView() {
		await tick();
		if (!emailListEl || emailHighlightIndex < 0) return;
		const items = emailListEl.querySelectorAll('[data-email-item]');
		items[emailHighlightIndex]?.scrollIntoView({ block: 'nearest' });
	}

	function handleEmailKeydown(e: KeyboardEvent, fieldName: string) {
		if (emailDropdownField !== fieldName || emailSuggestions.length === 0) return;
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			emailHighlightIndex = emailHighlightIndex < emailSuggestions.length - 1
				? emailHighlightIndex + 1
				: 0;
			scrollHighlightedIntoView();
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			emailHighlightIndex = emailHighlightIndex > 0
				? emailHighlightIndex - 1
				: emailSuggestions.length - 1;
			scrollHighlightedIntoView();
		} else if (e.key === 'Enter') {
			e.preventDefault();
			if (emailHighlightIndex >= 0) {
				selectEmailSuggestion(fieldName, emailSuggestions[emailHighlightIndex]);
			}
		} else if (e.key === 'Escape') {
			e.preventDefault();
			emailSuggestions = [];
			emailDropdownField = null;
			emailHighlightIndex = -1;
		} else if (e.key === 'Tab' && emailHighlightIndex >= 0) {
			e.preventDefault();
			selectEmailSuggestion(fieldName, emailSuggestions[emailHighlightIndex]);
		}
	}

	// Derived: platforms filtered by connected status
	const visiblePlatforms = $derived(
		connectionsLoaded && platformsLoaded
			? allPlatforms.filter((p) => connections.some((c) => c.platform === p.id && c.status === 'connected'))
			: allPlatforms
	);

	// Derived: connections for the active platform
	const platformConnections = $derived(
		connections.filter((c) => c.platform === activePlatform && c.status === 'connected')
	);

	// Derived: current platform object
	const currentPlatform = $derived(
		allPlatforms.find((p) => p.id === activePlatform)
	);

	// Derived: available actions for current platform
	const availableActions = $derived(currentPlatform?.actions ?? []);

	// Derived: current action object
	const currentAction = $derived(
		availableActions.find((a) => a.action_type === selectedActionType) ?? availableActions[0]
	);

	// Derived: fields to render
	const activeFields = $derived(currentAction?.fields ?? []);

	// Sync state from compose store when modal opens
	$effect(() => {
		if ($compose.isOpen) {
			loadPlatforms();
			loadConnections();
		}
	});

	// Set initial platform/action/prefill once when data is ready
	$effect(() => {
		if ($compose.isOpen && platformsLoaded && !initialSyncDone) {
			activePlatform = $compose.platform || visiblePlatforms[0]?.id || '';
			syncActionType();
			prefillFields();
			initialSyncDone = true;
		}
	});

	function syncActionType() {
		const storeAction = $compose.actionType;
		const platform = allPlatforms.find((p) => p.id === activePlatform);
		if (!platform) return;

		// Try to match store action to an available action_type
		const match = platform.actions.find((a) => a.action_type === storeAction);
		if (match) {
			selectedActionType = match.action_type;
		} else if (platform.actions.length > 0) {
			selectedActionType = platform.actions[0].action_type;
		}
	}

	// Auto-select connection when platform connections change — prefer the one resolved by the backend for the card's space
	$effect(() => {
		if (platformConnections.length > 0 && !platformConnections.find((c) => c.connection_id === selectedConnectionId)) {
			const preferred = $compose.connectionId;
			const match = preferred ? platformConnections.find((c) => c.connection_id === preferred) : undefined;
			selectedConnectionId = (match ?? platformConnections[0]).connection_id;
		}
	});

	function prefillFields() {
		const pf = $compose.prefill;
		const newValues: Record<string, string> = {};
		for (const [key, val] of Object.entries(pf)) {
			if (val != null && val !== '') {
				newValues[key] = String(val);
			}
		}
		formValues = newValues;
	}

	async function loadPlatforms() {
		if (platformsLoaded) return;
		try {
			const resp = await engineApi.getComposePlatforms();
			allPlatforms = resp.platforms;
		} catch {
			// Silently fail
		} finally {
			platformsLoaded = true;
		}
	}

	async function loadConnections() {
		if (connectionsLoaded) return;
		try {
			const resp = await engineApi.listEgressConnections();
			connections = resp.connections;
		} catch {
			// Silently fail; show all tabs
		} finally {
			connectionsLoaded = true;
		}
	}

	function buildPayload(): Record<string, unknown> {
		const payload: Record<string, unknown> = {};
		for (const [key, val] of Object.entries(formValues)) {
			if (val && val.trim()) {
				payload[key] = val;
			}
		}
		return payload;
	}

	function switchPlatform(platformId: string) {
		activePlatform = platformId;
		error = null;
		const platform = allPlatforms.find((p) => p.id === platformId);
		if (platform && platform.actions.length > 0) {
			selectedActionType = platform.actions[0].action_type;
		}
		formValues = {};
	}

	function switchAction(actionType: string) {
		selectedActionType = actionType;
		formValues = {};
	}

	const submitLabel = $derived(
		currentAction?.label?.startsWith('Create') ? 'Create'
		: currentAction?.label?.startsWith('Send') ? 'Send'
		: currentAction?.label ?? 'Send'
	);

	async function aiAssist() {
		aiAssisting = true;
		error = null;
		try {
			const result = await engineApi.egressAiAssist({
				platform: activePlatform,
				action_type: selectedActionType,
				context: buildPayload()
			});
			const draft = result.draft;
			for (const [key, val] of Object.entries(draft)) {
				if (val && (!formValues[key] || !formValues[key].trim())) {
					formValues[key] = String(val);
				}
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'AI assist failed';
		} finally {
			aiAssisting = false;
		}
	}

	async function submit() {
		sending = true;
		error = null;
		try {
			const result = await engineApi.egressExecute({
				platform: activePlatform,
				action_type: selectedActionType,
				payload: buildPayload(),
				connection_id: selectedConnectionId || undefined,
				source_card_id: $compose.sourceCardId ?? undefined,
				source_event_id: $compose.sourceEventId ?? undefined
			});
			success = true;
			resultUrl = result.result_url ?? null;
			setTimeout(() => {
				compose.closeCompose();
				resetState();
			}, 2000);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to send';
		} finally {
			sending = false;
		}
	}

	function resetState() {
		success = false;
		resultUrl = null;
		error = null;
		sending = false;
		aiAssisting = false;
		selectedConnectionId = '';
		formValues = {};
		initialSyncDone = false;
	}

	function close() {
		compose.closeCompose();
		resetState();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') close();
		// Cmd+Enter (mac) / Ctrl+Enter (win/linux) submits the compose form. Mirrors
		// the submit button's disabled state so a fast keystroke can't double-fire
		// while a send is in flight or the success splash is up.
		if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey) {
			if (sending || success) return;
			e.preventDefault();
			submit();
		}
	}

	$effect(() => {
		if (!$compose.isOpen) return;
		window.addEventListener('keydown', handleKeydown);
		return () => window.removeEventListener('keydown', handleKeydown);
	});

	function handleBackdrop(e: MouseEvent) {
		// Intentionally no-op: modal only closes via close button or ESC key.
		// Backdrop kept as event sink so clicks outside the card don't bleed through.
	}

	// Common input styles
	const glassInputBase = 'border-surface-600/40 bg-surface-800/40 backdrop-blur-sm';
	const solidInputBase = 'border-surface-600 bg-surface-800';
	const inputClass = $derived(`w-full rounded-md border ${$glassTheme ? glassInputBase : solidInputBase} px-3 py-2 text-sm text-surface-200 placeholder-surface-600 focus:border-laya-orange/50 focus:outline-none focus:ring-1 focus:ring-laya-orange/30`);
	const labelClass = 'block text-xs font-medium text-surface-400 mb-1';
	const selectClass = $derived(`rounded-md border ${$glassTheme ? glassInputBase : solidInputBase} px-3 py-2 text-sm text-surface-200 focus:border-laya-orange/50 focus:outline-none focus:ring-1 focus:ring-laya-orange/30`);
</script>

{#if $compose.isOpen}
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
		role="dialog"
		aria-label="Compose message"
		tabindex="0"
		onclick={handleBackdrop}
		onkeydown={handleKeydown}
	>
		<div class="mx-4 w-full max-w-2xl rounded-xl border {$glassTheme ? 'glass-card border-surface-700/40' : 'border-surface-700 bg-surface-900 shadow-2xl'}">
			<!-- Header -->
			<div class="flex items-center justify-between border-b px-5 py-3 {$glassTheme ? 'border-surface-700/40' : 'border-surface-700'}">
				<h2 class="text-sm font-semibold text-surface-50">Compose</h2>
				<button
					class="rounded p-1 text-surface-400 transition-colors hover:text-surface-200"
					onclick={close}
					aria-label="Close"
				>
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>

			<!-- Platform tabs -->
			<!-- scrollbar-none: Tailwind v4 utility that hides scrollbar across browsers -->
			<div class="flex gap-0.5 overflow-x-auto scrollbar-none border-b px-5 pt-2 {$glassTheme ? 'border-surface-700/40' : 'border-surface-700'}"
				style="-ms-overflow-style: none; scrollbar-width: none;"
			>
				{#each visiblePlatforms as platform}
					<button
						class="inline-flex shrink-0 items-center gap-1.5 rounded-t-md px-3 py-2 text-xs font-medium transition-colors
							{activePlatform === platform.id
								? 'bg-surface-800 text-laya-orange border-b-2 border-laya-orange'
								: 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'}"
						onclick={() => switchPlatform(platform.id)}
					>
						{platform.label}
					</button>
				{/each}
			</div>

			<!-- Form body -->
			<div class="p-5 space-y-3">
				{#if success}
					<div class="flex items-center gap-2 py-4 text-sm text-green-400">
						<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
						</svg>
						<span>Sent successfully!</span>
						{#if resultUrl}
							<a
								href={resultUrl}
								target="_blank"
								rel="noopener noreferrer"
								class="ml-1 text-laya-orange hover:text-laya-peach underline underline-offset-2 text-xs"
							>
								View
							</a>
						{/if}
					</div>
				{:else}
					<!-- Connection selector (when multiple connections for this platform) -->
					{#if platformConnections.length > 1}
						<div>
							<label class={labelClass} for="compose-connection">Account</label>
							<select id="compose-connection" bind:value={selectedConnectionId} class="{selectClass} w-full">
								{#each platformConnections as conn}
									<option value={conn.connection_id}>{conn.name}</option>
								{/each}
							</select>
						</div>
					{:else if platformConnections.length === 1}
						<div>
							<span class={labelClass}>Account</span>
							<p class="text-sm text-surface-300 px-3 py-2">{platformConnections[0].name}</p>
						</div>
					{/if}

					<!-- Action type selector (when platform has multiple composable actions) -->
					{#if availableActions.length > 1}
						<div>
							<label class={labelClass} for="compose-action">Action</label>
							<select id="compose-action" bind:value={selectedActionType} class="{selectClass} w-full" onchange={(e) => switchAction((e.target as HTMLSelectElement).value)}>
								{#each availableActions as action}
									<option value={action.action_type}>{action.label}</option>
								{/each}
							</select>
						</div>
					{/if}

					<!-- Dynamic fields from registry -->
					{#each activeFields as field (field.name)}
						<div>
							<label class={labelClass} for="compose-{field.name}">{field.label}</label>
							{#if field.type === 'textarea'}
								<textarea
									id="compose-{field.name}"
									bind:value={formValues[field.name]}
									rows="6"
									class="{inputClass} resize-y"
									placeholder={field.placeholder}
								></textarea>
							{:else if field.type === 'select' && field.options}
								<select
									id="compose-{field.name}"
									bind:value={formValues[field.name]}
									class="{selectClass} w-full"
								>
									{#each field.options as opt}
										<option value={opt}>{opt}</option>
									{/each}
								</select>
							{:else if field.type === 'email'}
								<div class="relative">
									<input
										id="compose-{field.name}"
										type="email"
										value={formValues[field.name] ?? ''}
										oninput={(e) => handleEmailInput(field.name, (e.target as HTMLInputElement).value)}
										onkeydown={(e) => handleEmailKeydown(e, field.name)}
										onfocusout={(e) => {
											const related = (e as FocusEvent).relatedTarget as HTMLElement | null;
											if (related?.closest('[data-email-dropdown]')) return;
											setTimeout(() => { emailDropdownField = null; emailHighlightIndex = -1; }, 100);
										}}
										class={inputClass}
										placeholder={field.placeholder}
										autocomplete="off"
									/>
									{#if emailDropdownField === field.name && emailSuggestions.length > 0}
										<!-- svelte-ignore a11y_no_static_element_interactions -->
										<div
											data-email-dropdown
											class="absolute z-50 mt-1 w-full rounded-md border {$glassTheme ? 'border-surface-600/40 bg-surface-900/95 backdrop-blur-md shadow-lg shadow-black/30' : 'border-surface-600 bg-surface-800 shadow-lg'}"
										>
											<div bind:this={emailListEl} class="max-h-40 overflow-y-auto py-1">
												{#each emailSuggestions as suggestion, i}
													<button
														type="button"
														data-email-item
														class="flex w-full px-3 py-1.5 text-left text-xs transition-colors {i === emailHighlightIndex ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-300 hover:bg-surface-700'}"
														onmousedown={(e) => { e.preventDefault(); selectEmailSuggestion(field.name, suggestion); }}
														onmouseenter={() => { emailHighlightIndex = i; }}
													>
														{suggestion}
													</button>
												{/each}
											</div>
										</div>
									{/if}
								</div>
							{:else}
								<input
									id="compose-{field.name}"
									type="text"
									bind:value={formValues[field.name]}
									class={inputClass}
									placeholder={field.placeholder}
								/>
							{/if}
						</div>
					{/each}

					{#if error}
						<p class="text-xs text-red-400">{error}</p>
					{/if}
				{/if}
			</div>

			<!-- Footer -->
			{#if !success}
				<div class="flex items-center justify-between border-t px-5 py-3 {$glassTheme ? 'border-surface-700/40' : 'border-surface-700'}">
					<button
						class="inline-flex items-center gap-1.5 rounded-md bg-surface-800 px-3 py-1.5 text-xs font-medium transition-colors
							{aiAssisting ? 'text-laya-orange cursor-wait' : 'text-surface-400 hover:text-laya-orange hover:bg-surface-700'}"
						onclick={aiAssist}
						disabled={aiAssisting || sending}
						title="Generate a draft with AI"
					>
						{#if aiAssisting}
							<svg class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
								<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
								<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
							</svg>
							Drafting...
						{:else}
							<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
							</svg>
							AI Assist
						{/if}
					</button>
					<div class="flex items-center gap-2">
						<button
							class="rounded-md px-3 py-1.5 text-xs text-surface-400 transition-colors hover:text-surface-200"
							onclick={close}
							disabled={sending}
						>
							Cancel
						</button>
						<button
							class="inline-flex items-center gap-1.5 rounded-md bg-laya-orange/20 px-4 py-1.5 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:opacity-50 disabled:cursor-not-allowed"
							onclick={submit}
							disabled={sending}
						>
							{#if sending}
								<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
									<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
									<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
								</svg>
								Sending...
							{:else}
								{submitLabel}
							{/if}
						</button>
					</div>
				</div>
			{/if}
		</div>
	</div>
{/if}
