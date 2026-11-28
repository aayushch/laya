<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { untrack } from 'svelte';
	import { engineApi } from '$lib/api/engine';
	import { glassTheme } from '$lib/stores/glassTheme';
	import PlatformIcon from './PlatformIcon.svelte';
	import SmtpSetupForm from './SmtpSetupForm.svelte';
	import TagInput from './TagInput.svelte';
	import type { EgressConnection, FieldDef } from '$lib/api/types';

	let {
		platform,
		platformLabel,
		isOAuth = false,
		fields = [],
		mode = 'add',
		connection,
		onClose,
		onConnected
	}: {
		platform: string;
		platformLabel: string;
		isOAuth?: boolean;
		fields?: FieldDef[];
		/** 'add' creates a new account. 'reconnect' re-authorizes `connection`
		 *  in place over OAuth: same id, name, workflows and history. */
		mode?: 'add' | 'reconnect';
		connection?: EgressConnection;
		onClose: () => void;
		onConnected: () => void;
	} = $props();

	const reconnecting = $derived(mode === 'reconnect' && !!connection);

	// Account name. A reconnect shows the existing name, read-only. The modal
	// is created per open, so the initial value is all it needs.
	let connectionName = $state(untrack(() => connection?.name ?? ''));
	let existingNames = $state<string[]>([]);
	let nameError = $state<string | null>(null);
	const nameTaken = $derived(
		!reconnecting && !!connectionName.trim() && existingNames.includes(connectionName.trim())
	);

	// API-key form state (checkbox fields hold booleans, everything else strings)
	let fieldValues = $state<Record<string, string | boolean>>({});
	let submitting = $state(false);
	let error = $state<string | null>(null);

	// Load existing names for uniqueness validation
	$effect(() => {
		engineApi
			.getConnectionNames(platform)
			.then((r) => {
				existingNames = r.names;
			})
			.catch(() => {});
	});

	// Slack channel selection
	let slackChannels = $state<string[]>([]);

	// Trello boards to monitor. Board short links are case-sensitive, so only
	// surrounding whitespace is trimmed.
	let trelloBoards = $state<string[]>([]);
	const keepBoardCase = (raw: string) => raw.trim();

	// OAuth state
	let oauthPolling = $state(false);
	let oauthError = $state<string | null>(null);
	let showOAuthSetup = $state(false);
	let oauthClientId = $state('');
	let oauthClientSecret = $state('');

	// Initialize field values
	$effect(() => {
		const vals: Record<string, string | boolean> = {};
		for (const f of fields) {
			vals[f.key] = f.type === 'checkbox' ? false : '';
		}
		fieldValues = vals;
	});

	/** Validate the account name for a new account. Returns false and sets the error if invalid. */
	function checkName(): boolean {
		if (reconnecting) return true;
		if (!connectionName.trim()) {
			nameError = 'Please provide a name for this account';
			return false;
		}
		if (nameTaken) {
			nameError = 'This name is already in use';
			return false;
		}
		nameError = null;
		return true;
	}

	async function handleApiKeySubmit() {
		if (!checkName()) return;
		if (platform === 'trello' && trelloBoards.length === 0) {
			error = 'Please add at least one board to monitor.';
			return;
		}
		submitting = true;
		error = null;
		try {
			await engineApi.createEgressConnection({
				platform,
				name: connectionName.trim() || undefined,
				credentials: fieldValues,
				boards: platform === 'trello' ? trelloBoards : undefined
			});
			onConnected();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Connection failed';
		} finally {
			submitting = false;
		}
	}

	async function handleSmtpSubmit(credentials: Record<string, string>) {
		submitting = true;
		error = null;
		try {
			await engineApi.createEgressConnection({
				platform: 'smtp',
				name: connectionName.trim() || `Email (${credentials.email})`,
				credentials
			});
			onConnected();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Connection failed';
		} finally {
			submitting = false;
		}
	}

	async function handleOAuthConnect() {
		if (!checkName()) return;
		if (!reconnecting && platform === 'slack' && slackChannels.length === 0) {
			oauthError = 'Please specify at least one channel to monitor.';
			return;
		}
		oauthError = null;
		try {
			const result = reconnecting
				? await engineApi.startOAuthFlow(platform, undefined, undefined, undefined, connection!.connection_id)
				: await engineApi.startOAuthFlow(
						platform,
						connectionName.trim() || undefined,
						undefined,
						platform === 'slack' ? slackChannels : undefined
					);
			// Open in system browser (works in Tauri and dev).
			// Tauri's shell plugin opens the default browser; in dev/web
			// we fall back to window.open.
			try {
				const { open } = await import('@tauri-apps/plugin-shell');
				await open(result.auth_url);
			} catch {
				window.open(result.auth_url, '_blank', 'width=600,height=700');
			}
			// Capture current connection count before polling
			try {
				const current = await engineApi.listEgressConnections();
				initialConnectionCount = current.connections.filter((c) => c.platform === platform).length;
			} catch {
				initialConnectionCount = 0;
			}
			// Start polling for completion
			oauthPolling = true;
			pollForOAuthCompletion();
		} catch (e) {
			const msg = e instanceof Error ? e.message : 'OAuth failed';
			if (msg.includes('not configured') || msg.includes('client')) {
				showOAuthSetup = true;
				oauthError = null;
			} else {
				oauthError = msg;
			}
		}
	}

	let pollInterval: ReturnType<typeof setInterval> | null = null;
	let pollCount = 0;
	let initialConnectionCount = 0;

	// Completion check while the browser sign-in is open. A new account shows
	// up as one more connection for the platform. A reconnect keeps the count
	// the same, so it waits for that connection to turn 'connected'; if the
	// re-authorization finished but left a different error, that error is shown.
	function pollForOAuthCompletion() {
		pollCount = 0;
		const initialError = connection?.error_message;
		pollInterval = setInterval(async () => {
			pollCount++;
			if (pollCount > 30) {
				// 60 seconds timeout
				stopPolling();
				oauthPolling = false;
				oauthError = 'OAuth flow timed out. Please try again.';
				return;
			}
			try {
				const conns = await engineApi.listEgressConnections();
				const platformConns = conns.connections.filter((c) => c.platform === platform);
				if (reconnecting) {
					const updated = platformConns.find((c) => c.connection_id === connection!.connection_id);
					if (updated?.status === 'connected') {
						stopPolling();
						oauthPolling = false;
						onConnected();
					} else if (updated && updated.error_message !== initialError) {
						stopPolling();
						oauthPolling = false;
						oauthError = updated.error_message || 'Reconnect failed';
					}
				} else if (platformConns.length > initialConnectionCount) {
					stopPolling();
					oauthPolling = false;
					onConnected();
				}
			} catch {
				// ignore polling errors
			}
		}, 2000);
	}

	function stopPolling() {
		if (pollInterval) {
			clearInterval(pollInterval);
			pollInterval = null;
		}
	}

	function close() {
		stopPolling();
		onClose();
	}

	// Slack apps with PKCE enabled are public clients and need no secret, so the
	// secret field is optional for Slack and required for every other provider.
	const secretOptional = $derived(platform === 'slack');
	const oauthSetupReady = $derived(
		!!oauthClientId.trim() && (secretOptional || !!oauthClientSecret.trim())
	);

	async function handleOAuthSetup() {
		if (!oauthSetupReady) return;
		submitting = true;
		oauthError = null;
		try {
			await engineApi.setupOAuthClient({
				platform,
				client_id: oauthClientId.trim(),
				client_secret: oauthClientSecret.trim()
			});
			showOAuthSetup = false;
			// Now try the OAuth flow again
			await handleOAuthConnect();
		} catch (e) {
			oauthError = e instanceof Error ? e.message : 'Setup failed';
		} finally {
			submitting = false;
		}
	}

	// Cleanup on unmount
	$effect(() => {
		return () => stopPolling();
	});

	const inputClass =
		'w-full rounded-lg border border-integ-input-border bg-integ-input-bg px-3 py-[9px] text-integ-input text-integ-t1 outline-none placeholder:text-integ-t4 focus:border-laya-orange focus:ring-[3px] focus:ring-integ-accent-tint';
	const labelClass = 'mb-1.5 block text-integ-note font-medium text-integ-t3';
	const helpClass = 'mt-1.5 text-integ-small leading-normal text-integ-t4';
	const primaryClass =
		'w-full rounded-[9px] bg-laya-orange px-4 py-2.5 text-center text-integ-input font-semibold text-integ-on-accent transition-colors hover:bg-laya-gold disabled:opacity-45 disabled:hover:bg-laya-orange';
	const errorBoxClass =
		'rounded-lg bg-[rgb(239_68_68/0.10)] px-3 py-2 text-integ-note text-integ-err-fg';
</script>

<svelte:window
	onkeydown={(e) => {
		if (e.key === 'Escape') close();
	}}
/>

<!-- Overlay -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="fixed inset-0 z-50 flex items-center justify-center bg-integ-scrim p-6" onclick={close}>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<div
		role="dialog"
		tabindex="-1"
		aria-modal="true"
		aria-labelledby="connect-modal-title"
		class="integ-modal w-full max-w-[440px] overflow-hidden rounded-[14px] shadow-[0_12px_48px_-8px_rgb(0_0_0/0.45)]
			{$glassTheme
				? 'glass-dropdown border-[0.5px] border-integ-input-border'
				: 'border border-surface-700 bg-integ-modal'}"
		onclick={(e) => e.stopPropagation()}
	>
		<!-- Header -->
		<div class="flex items-center gap-3 border-b-[0.5px] border-integ-line px-[22px] py-4">
			<div class="grid h-8 w-8 place-items-center rounded-lg bg-integ-fill-2 text-integ-t1">
				<PlatformIcon {platform} size={16} />
			</div>
			<div class="flex flex-1 flex-col gap-0.5">
				<h3 id="connect-modal-title" class="text-integ-name font-semibold text-integ-t1">
					{reconnecting ? `Reconnect ${platformLabel} · ${connection!.name}` : `Connect ${platformLabel}`}
				</h3>
				<p class="text-integ-note text-integ-t4">
					{#if isOAuth}
						Authenticate via OAuth
					{:else if platform === 'smtp'}
						Configure email server settings
					{:else}
						Enter your API credentials
					{/if}
				</p>
			</div>
			<button
				aria-label="Close"
				onclick={close}
				class="px-1 py-0.5 text-[20px] leading-none text-integ-t4 transition-colors hover:text-integ-t1"
			>
				×
			</button>
		</div>

		{#if oauthPolling}
			<div class="flex flex-col items-center gap-2.5 px-[22px] pb-[30px] pt-[34px]">
				<span class="h-7 w-7 animate-spin rounded-full border-2 border-integ-line border-t-laya-orange"></span>
				<span class="text-integ-input font-medium text-integ-t2">Waiting for authorization…</span>
				<span class="text-integ-note text-integ-t4">Finish signing in to {platformLabel} in your browser.</span>
				<button
					onclick={close}
					class="mt-1.5 text-integ-body text-integ-t3 transition-colors hover:text-integ-t1"
				>
					Cancel
				</button>
			</div>
		{:else}
			<!-- Body -->
			<div class="flex flex-col gap-4 px-[22px] pb-[22px] pt-5">
				{#if error}
					<div class={errorBoxClass}>{error}</div>
				{/if}

				<!-- Account name input -->
				<div>
					<label for="connection-name" class={labelClass}>Account name</label>
					<input
						id="connection-name"
						type="text"
						bind:value={connectionName}
						readonly={reconnecting}
						aria-invalid={nameTaken || !!nameError}
						placeholder="e.g. Personal, Work"
						class="{inputClass} {reconnecting ? 'text-integ-t3 focus:border-integ-input-border focus:ring-0' : ''}"
					/>
					{#if reconnecting}
						<p class={helpClass}>Re-authorizes this account in place. Its name, workflows and history are kept.</p>
					{:else if nameError}
						<p class="{helpClass} !text-integ-err-fg">{nameError}</p>
					{:else if nameTaken}
						<p class="{helpClass} !text-integ-err-fg">This name is already in use</p>
					{:else}
						<p class={helpClass}>A label to identify this account</p>
					{/if}
				</div>

				{#if platform === 'slack' && isOAuth && !reconnecting}
					<div>
						<span class={labelClass}>Channels to monitor</span>
						<TagInput bind:tags={slackChannels} placeholder="e.g., general, dev, team-standup" />
						<p class={helpClass}>Only these channels will be monitored.</p>
					</div>
				{/if}

				{#if platform === 'smtp'}
					<SmtpSetupForm onSubmit={handleSmtpSubmit} {submitting} />
				{:else if isOAuth}
					{#if showOAuthSetup}
						<div class="flex flex-col gap-4">
							<p class="text-integ-body text-integ-t3">
								To connect {platformLabel}, first configure your OAuth application credentials.
							</p>
							<div>
								<label for="oauth-client-id" class={labelClass}>Client ID</label>
								<input
									id="oauth-client-id"
									type="text"
									bind:value={oauthClientId}
									placeholder="Your OAuth client ID"
									class={inputClass}
								/>
							</div>
							<div>
								<label for="oauth-client-secret" class={labelClass}>
									Client Secret{#if secretOptional}
										<span class="font-normal text-integ-t4"> (optional)</span>{/if}
								</label>
								<input
									id="oauth-client-secret"
									type="password"
									bind:value={oauthClientSecret}
									placeholder="Your OAuth client secret"
									class={inputClass}
								/>
								{#if secretOptional}
									<p class={helpClass}>Leave empty if your Slack app has PKCE enabled.</p>
								{/if}
							</div>
							{#if oauthError}
								<div class={errorBoxClass}>{oauthError}</div>
							{/if}
							<button onclick={handleOAuthSetup} disabled={submitting || !oauthSetupReady} class={primaryClass}>
								{submitting ? 'Saving…' : 'Save & Continue'}
							</button>
						</div>
					{:else}
						{#if oauthError}
							<div class={errorBoxClass}>{oauthError}</div>
						{/if}
						<button onclick={handleOAuthConnect} disabled={nameTaken} class={primaryClass}>
							{reconnecting ? 'Sign in again' : 'Continue'} with {platformLabel}
						</button>
						<button
							onclick={() => {
								showOAuthSetup = true;
								oauthError = null;
							}}
							class="self-center text-integ-note text-integ-t4 transition-colors hover:text-integ-t2"
						>
							Change OAuth credentials
						</button>
					{/if}
				{:else}
					<!-- API-key form -->
					{#each fields as field (field.key)}
						<div>
							{#if field.type === 'checkbox'}
								<label for="field-{field.key}" class="flex items-center gap-2 text-integ-note font-medium text-integ-t3">
									<input
										id="field-{field.key}"
										type="checkbox"
										checked={fieldValues[field.key] === true}
										onchange={(e) => (fieldValues[field.key] = e.currentTarget.checked)}
										class="h-4 w-4 rounded accent-laya-orange"
									/>
									{field.label}
								</label>
							{:else}
								<label for="field-{field.key}" class={labelClass}>{field.label}</label>
								<input
									id="field-{field.key}"
									type={field.type === 'password' ? 'password' : 'text'}
									bind:value={fieldValues[field.key]}
									placeholder={field.placeholder ?? ''}
									class={inputClass}
								/>
							{/if}
							{#if field.help}
								<p class={helpClass}>{field.help}</p>
							{/if}
						</div>
					{/each}

					{#if platform === 'trello'}
						<div>
							<span class={labelClass}>Boards to monitor</span>
							<TagInput
								bind:tags={trelloBoards}
								normalize={keepBoardCase}
								placeholder="e.g., https://trello.com/b/AbCd1234/board-name"
							/>
							<p class={helpClass}>Paste a board URL or ID. Only these boards will be monitored.</p>
						</div>
					{/if}

					<button
						onclick={handleApiKeySubmit}
						disabled={submitting ||
							nameTaken ||
							fields.some((f) => f.type !== 'checkbox' && !String(fieldValues[f.key] ?? '').trim())}
						class={primaryClass}
					>
						{submitting ? 'Connecting…' : 'Connect'}
					</button>
				{/if}
			</div>
		{/if}
	</div>
</div>
