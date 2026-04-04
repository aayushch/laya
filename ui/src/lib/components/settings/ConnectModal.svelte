<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import PlatformIcon from './PlatformIcon.svelte';
	import SmtpSetupForm from './SmtpSetupForm.svelte';
	import type { FieldDef } from '$lib/api/types';

	let {
		platform,
		platformLabel,
		isOAuth = false,
		fields = [],
		hasExistingConnections = false,
		onClose,
		onConnected
	}: {
		platform: string;
		platformLabel: string;
		isOAuth?: boolean;
		fields?: FieldDef[];
		hasExistingConnections?: boolean;
		onClose: () => void;
		onConnected: () => void;
	} = $props();

	// Connection name (for additional accounts)
	let connectionName = $state('');
	let existingNames = $state<string[]>([]);
	let nameError = $state<string | null>(null);

	// API-key form state
	let fieldValues = $state<Record<string, string>>({});
	let submitting = $state(false);
	let error = $state<string | null>(null);

	// Load existing names when adding another account
	$effect(() => {
		if (hasExistingConnections) {
			engineApi.getConnectionNames(platform).then(r => {
				existingNames = r.names;
			}).catch(() => {});
		}
	});

	// OAuth state
	let oauthPolling = $state(false);
	let oauthError = $state<string | null>(null);
	let showOAuthSetup = $state(false);
	let oauthClientId = $state('');
	let oauthClientSecret = $state('');

	// Initialize field values
	$effect(() => {
		const vals: Record<string, string> = {};
		for (const f of fields) {
			vals[f.key] = '';
		}
		fieldValues = vals;
	});

	async function handleApiKeySubmit() {
		if (hasExistingConnections && !connectionName.trim()) {
			nameError = 'Please provide a name for this account';
			return;
		}
		if (connectionName && existingNames.includes(connectionName.trim())) {
			nameError = 'This name is already in use';
			return;
		}
		nameError = null;
		submitting = true;
		error = null;
		try {
			await engineApi.createEgressConnection({
				platform,
				name: connectionName.trim() || undefined,
				credentials: fieldValues
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
				name: `Email (${credentials.email})`,
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
		if (hasExistingConnections && !connectionName.trim()) {
			nameError = 'Please provide a name for this account';
			return;
		}
		if (connectionName && existingNames.includes(connectionName.trim())) {
			nameError = 'This name is already in use';
			return;
		}
		nameError = null;
		oauthError = null;
		try {
			const result = await engineApi.startOAuthFlow(platform, connectionName.trim() || undefined);
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
				initialConnectionCount = current.connections.filter(c => c.platform === platform).length;
			} catch { initialConnectionCount = 0; }
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

	function pollForOAuthCompletion() {
		pollCount = 0;
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
				// Detect a new connection (count increased) rather than matching an existing one
				if (platformConns.length > initialConnectionCount) {
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

	async function handleOAuthSetup() {
		if (!oauthClientId.trim() || !oauthClientSecret.trim()) return;
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

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			stopPolling();
			onClose();
		}
	}

	// Cleanup on unmount
	$effect(() => {
		return () => stopPolling();
	});
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- Overlay -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onclick={onClose} onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="w-full max-w-md rounded-xl border border-surface-700 bg-surface-900 shadow-2xl"
		onclick={(e) => e.stopPropagation()}
		onkeydown={(e) => e.stopPropagation()}
	>
		<!-- Header -->
		<div class="flex items-center gap-3 border-b border-surface-700 px-6 py-4">
			<div class="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-800 text-surface-300">
				<PlatformIcon platform={platform} size={18} />
			</div>
			<div>
				<h3 class="text-sm font-semibold text-surface-100">Connect {platformLabel}</h3>
				<p class="text-xs text-surface-500">
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
				onclick={() => { stopPolling(); onClose(); }}
				class="ml-auto text-surface-500 hover:text-surface-200 transition-colors"
			>
				<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>

		<!-- Body -->
		<div class="px-6 py-5">
			{#if error}
				<div class="mb-4 rounded-md border border-red-800/50 bg-red-900/20 px-3 py-2 text-xs text-red-300">
					{error}
				</div>
			{/if}

			<!-- Account name input (when adding another account) -->
			{#if hasExistingConnections}
				<div class="mb-4">
					<label for="connection-name" class="mb-1 block text-xs font-medium text-surface-400">Account Name</label>
					<input
						id="connection-name"
						type="text"
						bind:value={connectionName}
						placeholder="e.g., Personal, Work"
						class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
					/>
					{#if nameError}
						<p class="mt-1 text-[11px] text-red-400">{nameError}</p>
					{:else if connectionName && existingNames.includes(connectionName.trim())}
						<p class="mt-1 text-[11px] text-red-400">This name is already in use</p>
					{:else}
						<p class="mt-1 text-[11px] text-surface-500">A label to identify this account</p>
					{/if}
				</div>
			{/if}

			{#if platform === 'smtp'}
				<!-- SMTP setup form -->
				<SmtpSetupForm onSubmit={handleSmtpSubmit} {submitting} />

			{:else if isOAuth}
				<!-- OAuth flow -->
				{#if showOAuthSetup}
					<div class="space-y-4">
						<p class="text-xs text-surface-400">
							To connect {platformLabel}, first configure your OAuth application credentials.
						</p>
						<div>
							<label for="oauth-client-id" class="mb-1 block text-xs font-medium text-surface-400">Client ID</label>
							<input
								id="oauth-client-id"
								type="text"
								bind:value={oauthClientId}
								placeholder="Your OAuth client ID"
								class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
							/>
						</div>
						<div>
							<label for="oauth-client-secret" class="mb-1 block text-xs font-medium text-surface-400">Client Secret</label>
							<input
								id="oauth-client-secret"
								type="password"
								bind:value={oauthClientSecret}
								placeholder="Your OAuth client secret"
								class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
							/>
						</div>
						{#if oauthError}
							<div class="rounded-md border border-red-800/50 bg-red-900/20 px-3 py-2 text-xs text-red-300">
								{oauthError}
							</div>
						{/if}
						<button
							onclick={handleOAuthSetup}
							disabled={submitting || !oauthClientId.trim() || !oauthClientSecret.trim()}
							class="w-full rounded-md bg-laya-orange px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-laya-gold disabled:opacity-50"
						>
							{submitting ? 'Saving...' : 'Save & Continue'}
						</button>
					</div>
				{:else if oauthPolling}
					<div class="flex flex-col items-center gap-3 py-6">
						<div class="h-8 w-8 animate-spin rounded-full border-2 border-surface-600 border-t-laya-orange"></div>
						<p class="text-sm text-surface-300">Waiting for authorization...</p>
						<p class="text-xs text-surface-500">Complete the sign-in in the opened window</p>
					</div>
				{:else}
					<div class="flex flex-col items-center gap-4 py-4">
						<p class="text-center text-sm text-surface-400">
							Click below to sign in with {platformLabel}. A new window will open for authorization.
						</p>
						{#if oauthError}
							<div class="w-full rounded-md border border-red-800/50 bg-red-900/20 px-3 py-2 text-xs text-red-300">
								{oauthError}
							</div>
						{/if}
						<button
							onclick={handleOAuthConnect}
							class="flex items-center gap-2 rounded-md bg-laya-orange px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-laya-gold"
						>
							<PlatformIcon platform={platform} size={16} />
							Connect {platformLabel}
						</button>
						<button
							onclick={() => { showOAuthSetup = true; oauthError = null; }}
							class="text-xs text-surface-500 hover:text-surface-300 transition-colors"
						>
							Change OAuth credentials
						</button>
					</div>
				{/if}

			{:else}
				<!-- API-key form -->
				<div class="space-y-4">
					{#each fields as field}
						<div>
							<label for="field-{field.key}" class="mb-1 block text-xs font-medium text-surface-400">{field.label}</label>
							{#if field.type === 'password'}
								<input
									id="field-{field.key}"
									type="password"
									bind:value={fieldValues[field.key]}
									placeholder={field.placeholder ?? ''}
									class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
								/>
							{:else}
								<input
									id="field-{field.key}"
									type="text"
									bind:value={fieldValues[field.key]}
									placeholder={field.placeholder ?? ''}
									class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
								/>
							{/if}
							{#if field.help}
								<p class="mt-1 text-[11px] text-surface-500">{field.help}</p>
							{/if}
						</div>
					{/each}

					<button
						onclick={handleApiKeySubmit}
						disabled={submitting || fields.some((f) => !fieldValues[f.key]?.trim())}
						class="w-full rounded-md bg-laya-orange px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-laya-gold disabled:opacity-50"
					>
						{submitting ? 'Connecting...' : 'Connect'}
					</button>
				</div>
			{/if}
		</div>
	</div>
</div>
