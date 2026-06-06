<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { compose } from '$lib/stores/compose';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';
	import { tick } from 'svelte';
	import type { EgressConnection, ComposePlatform, ComposeAction, ComposeField, ComposeFieldAutocomplete } from '$lib/api/types';
	import DateTimePicker from './DateTimePicker.svelte';
	import Dropdown from '$lib/components/Dropdown.svelte';

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
	// Chips for multi-value email fields (to, cc, bcc)
	let emailChips: Record<string, string[]> = $state({});

	let activePlatform = $state('');
	let selectedActionType = $state('');
	let initialSyncDone = $state(false);

	// Timezone for calendar datetime fields — auto-set to browser timezone
	let composeTz = $state(Intl.DateTimeFormat().resolvedOptions().timeZone);
	let tzSearchQuery = $state('');
	let tzDropdownOpen = $state(false);
	let tzTriggerRef = $state<HTMLButtonElement | undefined>();
	let tzPanelRef = $state<HTMLDivElement | undefined>();
	let tzSearchRef = $state<HTMLInputElement | undefined>();
	let tzPos = $state({ top: 0, left: 0, width: 0 });

	// Email autocomplete
	let emailSuggestions = $state<string[]>([]);
	let emailDropdownField = $state<string | null>(null);
	let emailDebounceTimer = $state<ReturnType<typeof setTimeout> | null>(null);
	let emailHighlightIndex = $state(-1);
	let emailListEl = $state<HTMLDivElement | undefined>();
	let emailDropdownPos = $state({ top: 0, left: 0, width: 0 });

	function addEmailChip(fieldName: string, email: string) {
		const current = emailChips[fieldName] ?? [];
		if (!current.includes(email)) {
			emailChips[fieldName] = [...current, email];
		}
	}

	function removeEmailChip(fieldName: string, index: number) {
		const current = emailChips[fieldName] ?? [];
		emailChips[fieldName] = current.filter((_, i) => i !== index);
	}

	function isEmailField(fieldName: string): boolean {
		return activeFields.some(f => f.name === fieldName && f.type === 'email');
	}

	function handleAutocompleteInput(fieldName: string, value: string, ac: ComposeFieldAutocomplete) {
		if (emailDebounceTimer) clearTimeout(emailDebounceTimer);

		// For email fields, comma creates chips
		if (isEmailField(fieldName) && value.includes(',')) {
			const parts = value.split(',').map(s => s.trim());
			const lastPart = parts.pop() ?? '';
			for (const part of parts) {
				if (part) addEmailChip(fieldName, part);
			}
			formValues[fieldName] = lastPart;
			value = lastPart;
			if (value.trim().length < 2) {
				emailSuggestions = [];
				emailDropdownField = null;
				return;
			}
		} else {
			formValues[fieldName] = value;
		}

		if (value.trim().length < 2) {
			emailSuggestions = [];
			emailDropdownField = null;
			return;
		}
		emailDebounceTimer = setTimeout(async () => {
			try {
				const resp = await engineApi.fieldSuggestions(
					value.trim(),
					ac.scope,
					ac.scope === 'platform' ? activePlatform : '',
					ac.sources
				);
				emailSuggestions = resp.suggestions;
				emailDropdownField = resp.suggestions.length > 0 ? fieldName : null;
				emailHighlightIndex = -1;
				if (emailDropdownField) {
					const el = document.getElementById(`compose-${fieldName}`);
					if (el) {
						const container = el.closest('[data-email-container]') ?? el;
						const r = container.getBoundingClientRect();
						emailDropdownPos = { top: r.bottom + 4, left: r.left, width: r.width };
					}
				}
			} catch {
				emailSuggestions = [];
				emailDropdownField = null;
			}
		}, 250);
	}

	function selectEmailSuggestion(fieldName: string, value: string) {
		if (isEmailField(fieldName)) {
			addEmailChip(fieldName, value);
			formValues[fieldName] = '';
		} else {
			formValues[fieldName] = value;
		}
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
		// Backspace on empty input removes the last chip
		if (e.key === 'Backspace' && !(formValues[fieldName] ?? '')) {
			const chips = emailChips[fieldName];
			if (chips && chips.length > 0) {
				emailChips[fieldName] = chips.slice(0, -1);
				e.preventDefault();
				return;
			}
		}
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

	const hasDatetimeFields = $derived(activeFields.some(f => f.type === 'datetime-local'));

	function formatTzOffset(tz: string): string {
		try {
			const parts = new Intl.DateTimeFormat('en', { timeZone: tz, timeZoneName: 'shortOffset' }).formatToParts(new Date());
			return parts.find(p => p.type === 'timeZoneName')?.value ?? '';
		} catch { return ''; }
	}

	const ALL_TIMEZONES: string[] = (() => {
		try { return Intl.supportedValuesOf('timeZone'); }
		catch { return []; }
	})();

	const filteredTimezones = $derived(
		tzSearchQuery.trim().length === 0
			? ALL_TIMEZONES.slice(0, 30)
			: ALL_TIMEZONES.filter(tz => tz.toLowerCase().includes(tzSearchQuery.toLowerCase())).slice(0, 30)
	);

	function positionTzPanel() {
		if (!tzTriggerRef) return;
		const r = tzTriggerRef.getBoundingClientRect();
		const spaceBelow = window.innerHeight - r.bottom;
		const panelH = 240;
		tzPos = {
			top: spaceBelow < panelH && r.top > spaceBelow ? r.top - panelH - 4 : r.bottom + 4,
			left: r.left,
			width: Math.max(r.width, 200),
		};
	}

	function toggleTzDropdown() {
		if (tzDropdownOpen) { tzDropdownOpen = false; return; }
		tzSearchQuery = '';
		positionTzPanel();
		tzDropdownOpen = true;
		requestAnimationFrame(() => tzSearchRef?.focus());
	}

	function handleTzWindowClick(e: MouseEvent) {
		if (!tzDropdownOpen) return;
		const target = e.target as Node;
		if (tzTriggerRef?.contains(target)) return;
		if (tzPanelRef?.contains(target)) return;
		tzDropdownOpen = false;
	}

	$effect(() => {
		if (!tzDropdownOpen) return;
		window.addEventListener('mousedown', handleTzWindowClick, true);
		return () => window.removeEventListener('mousedown', handleTzWindowClick, true);
	});

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
		const newChips: Record<string, string[]> = {};
		for (const [key, val] of Object.entries(pf)) {
			if (val != null && val !== '') {
				const strVal = String(val);
				const field = activeFields.find(f => f.name === key);
				if (field?.type === 'email' && strVal.includes(',')) {
					newChips[key] = strVal.split(',').map(s => s.trim()).filter(Boolean);
					newValues[key] = '';
				} else {
					newValues[key] = strVal;
				}
			}
		}
		formValues = newValues;
		emailChips = newChips;
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
			const chips = emailChips[key];
			if (chips && chips.length > 0) {
				const all = [...chips];
				if (val && val.trim()) all.push(val.trim());
				payload[key] = all.join(', ');
			} else if (val && val.trim()) {
				payload[key] = val;
			}
		}
		for (const [key, chips] of Object.entries(emailChips)) {
			if (chips.length > 0 && !(key in payload)) {
				payload[key] = chips.join(', ');
			}
		}
		if (hasDatetimeFields) {
			payload.timezone = composeTz;
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
		emailChips = {};
	}

	function switchAction(actionType: string) {
		selectedActionType = actionType;
		formValues = {};
		emailChips = {};
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
				if (!val) continue;
				const hasChips = (emailChips[key]?.length ?? 0) > 0;
				const hasInput = formValues[key]?.trim();
				if (hasChips || hasInput) continue;
				const field = activeFields.find(f => f.name === key);
				if (field?.type === 'email' && String(val).includes(',')) {
					emailChips[key] = String(val).split(',').map(s => s.trim()).filter(Boolean);
					formValues[key] = '';
				} else {
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
		emailChips = {};
		initialSyncDone = false;
		composeTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
		tzDropdownOpen = false;
		tzSearchQuery = '';
	}

	function close() {
		compose.closeCompose();
		resetState();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			if (emailDropdownField) {
				e.preventDefault();
				emailSuggestions = [];
				emailDropdownField = null;
				emailHighlightIndex = -1;
				return;
			}
		}
		if (e.key === '.' && e.metaKey) { e.preventDefault(); close(); return; }
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
		// Intentionally no-op: modal only closes via close button or ⌘.
		// Backdrop kept as event sink so clicks outside the card don't bleed through.
	}

	// Common input styles
	const glassInputBase = 'border-surface-600/40 bg-surface-800/40 backdrop-blur-sm';
	const solidInputBase = 'border-surface-600 bg-surface-800';
	const inputClass = $derived(`w-full rounded-md border ${$glassTheme ? glassInputBase : solidInputBase} px-3 py-2 text-sm text-surface-200 placeholder-surface-600 focus:border-laya-orange/50 focus:outline-none focus:ring-1 focus:ring-laya-orange/30`);
	const labelClass = 'block text-xs font-medium text-surface-400 mb-1';
	const selectClass = $derived(`rounded-md border ${$glassTheme ? glassInputBase : solidInputBase} h-[38px] px-3 text-sm text-surface-200 focus:border-laya-orange/50 focus:outline-none focus:ring-1 focus:ring-laya-orange/30`);
	const emailContainerClass = $derived(`w-full rounded-md border ${$glassTheme ? glassInputBase : solidInputBase} px-2 py-1.5 flex flex-wrap items-center gap-1.5 cursor-text has-[:focus]:border-laya-orange/50 has-[:focus]:ring-1 has-[:focus]:ring-laya-orange/30`);
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
		<div class="mx-4 w-full max-w-2xl h-[700px] flex flex-col rounded-xl border {$glassTheme ? 'glass-card border-surface-700/40' : 'border-surface-700 bg-surface-900 shadow-2xl'}">
			<!-- Header -->
			<div class="flex shrink-0 items-center justify-between border-b px-5 py-3 {$glassTheme ? 'border-surface-700/40' : 'border-surface-700'}">
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
			<div class="flex shrink-0 gap-0.5 overflow-x-auto scrollbar-none border-b px-5 pt-2 {$glassTheme ? 'border-surface-700/40' : 'border-surface-700'}"
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
			<div class="p-5 space-y-3 flex-1 overflow-y-auto">
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
					<!-- Account + Action row -->
					<div class="flex gap-3">
						{#if platformConnections.length > 1}
							<div class="flex-1 min-w-0">
								<label class={labelClass} for="compose-connection">Account</label>
								<Dropdown
									id="compose-connection"
									bind:value={selectedConnectionId}
									options={platformConnections.map((c) => ({ value: c.connection_id, label: c.name }))}
									onchange={(v) => { selectedConnectionId = v; }}
									placeholder="Select account…"
								/>
							</div>
						{:else if platformConnections.length === 1}
							<div class="flex-1 min-w-0">
								<span class={labelClass}>Account</span>
								<p class="text-sm text-surface-300 px-3 py-2">{platformConnections[0].name}</p>
							</div>
						{/if}

						<div class="flex-1 min-w-0">
							<label class={labelClass} for="compose-action">Action</label>
							{#if availableActions.length > 1}
								<Dropdown
									id="compose-action"
									bind:value={selectedActionType}
									options={availableActions.map((a) => ({ value: a.action_type, label: a.label }))}
									onchange={(v) => switchAction(v)}
									placeholder="Select action…"
								/>
							{:else}
								<p id="compose-action" class="text-sm text-surface-300 px-3 py-2">{currentAction?.label ?? '—'}</p>
							{/if}
						</div>
					</div>

					<!-- Dynamic fields from registry -->
					{#each activeFields as field, i (field.name)}
						{#if field.type === 'datetime-local' && i > 0 && activeFields[i - 1]?.type === 'datetime-local'}
							<!-- Already rendered as part of the previous pair -->
						{:else if field.type === 'datetime-local'}
							{@const nextField = activeFields[i + 1]?.type === 'datetime-local' ? activeFields[i + 1] : null}
							<div class={nextField ? 'grid grid-cols-2 gap-3' : ''}>
								<div>
									<label class={labelClass} for="compose-{field.name}">{field.label}</label>
									<DateTimePicker
										id="compose-{field.name}"
										value={formValues[field.name] ?? ''}
										onchange={(v) => { formValues[field.name] = v; }}
									/>
								</div>
								{#if nextField}
									<div>
										<label class={labelClass} for="compose-{nextField.name}">{nextField.label}</label>
										<DateTimePicker
											id="compose-{nextField.name}"
											value={formValues[nextField.name] ?? ''}
											onchange={(v) => { formValues[nextField.name] = v; }}
										/>
									</div>
								{/if}
							</div>
							<!-- Timezone selector (shown once after the first datetime pair) -->
							{#if i === 0 || activeFields[i - 1]?.type !== 'datetime-local'}
								<div>
									<label class="{labelClass} flex items-center gap-1.5" for="compose-timezone">
										<svg class="h-3.5 w-3.5 text-surface-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-width="2"/><path stroke-width="2" d="M12 2a14.5 14.5 0 000 20 14.5 14.5 0 000-20M2 12h20"/></svg>
										Timezone
									</label>
									<button
										bind:this={tzTriggerRef}
										type="button"
										id="compose-timezone"
										class="flex w-full items-center justify-between rounded-md border px-3 h-[38px] text-sm transition-colors
											{$glassTheme
												? 'glass-input text-surface-200 hover:border-white/25'
												: 'border-surface-600 bg-surface-900 text-surface-200 hover:border-surface-500'}"
										onclick={toggleTzDropdown}
									>
										<span class="truncate">{composeTz.replace(/_/g, ' ')}</span>
										<span class="ml-2 shrink-0 text-xs text-surface-400">{formatTzOffset(composeTz)}</span>
									</button>
									{#if tzDropdownOpen}
										<div
											use:portal
											bind:this={tzPanelRef}
											class="fixed z-[100] rounded-lg border overflow-hidden
												{$glassTheme
													? 'glass-dropdown border-white/15'
													: 'border-surface-600 bg-surface-800 shadow-xl shadow-black/30'}"
											style="top: {tzPos.top}px; left: {tzPos.left}px; width: {tzPos.width}px;"
										>
											<input
												bind:this={tzSearchRef}
												type="text"
												class="w-full border-b px-3 py-2 text-sm placeholder-surface-500 focus:outline-none
													{$glassTheme
														? 'border-white/[0.08] bg-transparent text-surface-200'
														: 'border-surface-700 bg-surface-800 text-surface-200'}"
												placeholder="Search timezones…"
												bind:value={tzSearchQuery}
											/>
											<div class="max-h-48 overflow-y-auto p-1">
												{#each filteredTimezones as tz}
													{@const isSelected = tz === composeTz}
													<button
														type="button"
														class="flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-sm transition-colors
															{isSelected
																? ($glassTheme
																	? 'bg-white/[0.14] text-surface-100 font-medium'
																	: 'bg-surface-600 text-surface-100 font-medium')
																: ($glassTheme
																	? 'text-surface-300 hover:bg-white/[0.08]'
																	: 'text-surface-300 hover:bg-surface-700')}"
														onclick={() => { composeTz = tz; tzDropdownOpen = false; }}
													>
														<span class="truncate">{tz.replace(/_/g, ' ')}</span>
														<span class="ml-2 shrink-0 text-xs text-surface-500">{formatTzOffset(tz)}</span>
													</button>
												{/each}
												{#if filteredTimezones.length === 0}
													<div class="px-3 py-3 text-center text-sm text-surface-500">No matches</div>
												{/if}
											</div>
										</div>
									{/if}
								</div>
							{/if}
						{:else}
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
									<Dropdown
										id="compose-{field.name}"
										bind:value={formValues[field.name]}
										options={field.options.map((opt) => ({ value: opt, label: opt }))}
										onchange={(v) => { formValues[field.name] = v; }}
										placeholder={field.placeholder ?? 'Select…'}
									/>
								{:else if field.autocomplete}
									<div class="relative">
										{#if field.type === 'email'}
											<!-- Multi-value email field with chips -->
											<!-- svelte-ignore a11y_click_events_have_key_events -->
											<!-- svelte-ignore a11y_no_static_element_interactions -->
											<div
												data-email-container
												class={emailContainerClass}
												onclick={() => document.getElementById(`compose-${field.name}`)?.focus()}
											>
												{#each (emailChips[field.name] ?? []) as chip, idx}
													<span class="inline-flex items-center gap-0.5 rounded-full bg-surface-700 pl-2.5 pr-1 py-0.5 text-xs text-surface-200">
														{chip}
														<button
															type="button"
															class="rounded-full p-0.5 text-surface-400 hover:text-surface-200 transition-colors"
															onclick={(e) => { e.stopPropagation(); removeEmailChip(field.name, idx); }}
															aria-label="Remove {chip}"
														>
															<svg class="h-2.5 w-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
																<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
															</svg>
														</button>
													</span>
												{/each}
												<input
													id="compose-{field.name}"
													type="text"
													value={formValues[field.name] ?? ''}
													oninput={(e) => handleAutocompleteInput(field.name, (e.target as HTMLInputElement).value, field.autocomplete!)}
													onkeydown={(e) => handleEmailKeydown(e, field.name)}
													onfocusout={(e) => {
														const related = (e as FocusEvent).relatedTarget as HTMLElement | null;
														if (related?.closest('[data-email-dropdown]')) return;
														setTimeout(() => { emailDropdownField = null; emailHighlightIndex = -1; }, 100);
													}}
													class="flex-1 min-w-[120px] bg-transparent outline-none border-none p-0 text-sm text-surface-200 placeholder-surface-600"
													placeholder={(emailChips[field.name]?.length ?? 0) > 0 ? '' : field.placeholder}
													autocomplete="off"
												/>
											</div>
										{:else}
											<!-- Single-value autocomplete field -->
											<input
												id="compose-{field.name}"
												type="text"
												value={formValues[field.name] ?? ''}
												oninput={(e) => handleAutocompleteInput(field.name, (e.target as HTMLInputElement).value, field.autocomplete!)}
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
										{/if}
										{#if emailDropdownField === field.name && emailSuggestions.length > 0}
											<!-- svelte-ignore a11y_no_static_element_interactions -->
											<div
												use:portal
												data-email-dropdown
												class="fixed z-[100] rounded-md border {$glassTheme ? 'glass-menu' : 'border-surface-600 bg-surface-800 shadow-lg'}"
												style="top: {emailDropdownPos.top + 4}px; left: {emailDropdownPos.left + 4}px; width: {emailDropdownPos.width - 8}px;"
											>
												<div bind:this={emailListEl} class="max-h-40 overflow-y-auto py-1">
													{#each emailSuggestions as suggestion, si}
														<button
															type="button"
															data-email-item
															class="flex w-full px-3 py-1.5 text-left text-xs transition-colors {si === emailHighlightIndex ? 'bg-laya-orange/15 text-laya-orange' : 'text-surface-300 hover:bg-surface-700'}"
															onmousedown={(e) => { e.preventDefault(); selectEmailSuggestion(field.name, suggestion); }}
															onmouseenter={() => { emailHighlightIndex = si; }}
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
						{/if}
					{/each}

					{#if error}
						<p class="text-xs text-red-400">{error}</p>
					{/if}
				{/if}
			</div>

			<!-- Footer -->
			{#if !success}
				<div class="flex shrink-0 items-center justify-between border-t px-5 py-3 {$glassTheme ? 'border-surface-700/40' : 'border-surface-700'}">
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
			<div class="shrink-0 flex items-center justify-between px-5 pb-3 pt-1.5 text-[10px] text-surface-500">
				{#if !success && activeFields.some(f => f.type === 'textarea')}
					<p>AI Assist uses the <span class="text-surface-400">{activeFields.find(f => f.type === 'textarea')?.label ?? 'body'}</span> field as your prompt</p>
				{:else}
					<span></span>
				{/if}
				<p>Press <kbd class="rounded border border-surface-600 px-1 py-0.5 font-mono text-surface-400">⌘.</kbd> to close</p>
			</div>
		</div>
	</div>
{/if}
