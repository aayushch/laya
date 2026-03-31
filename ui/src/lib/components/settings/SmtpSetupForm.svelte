<script lang="ts">
	import { engineApi } from '$lib/api/engine';
	import type { EmailProviderDetection } from '$lib/api/types';

	let {
		onSubmit,
		submitting = false
	}: {
		onSubmit: (credentials: Record<string, string>) => void;
		submitting?: boolean;
	} = $props();

	let email = $state('');
	let password = $state('');
	let smtpHost = $state('');
	let smtpPort = $state('587');
	let imapHost = $state('');
	let imapPort = $state('993');
	let useTls = $state(true);
	let detecting = $state(false);
	let detection = $state<EmailProviderDetection | null>(null);
	let providerNote = $state('');
	let isOAuthRedirect = $state(false);
	let oauthRedirectPlatform = $state('');

	async function detectProvider() {
		if (!email.includes('@')) return;
		detecting = true;
		detection = null;
		isOAuthRedirect = false;
		try {
			const result = await engineApi.detectEmailProvider(email);
			detection = result;

			if (result.method === 'oauth') {
				isOAuthRedirect = true;
				oauthRedirectPlatform = result.redirect_platform ?? '';
				providerNote = result.note ?? '';
			} else {
				if (result.smtp_host) smtpHost = result.smtp_host;
				if (result.smtp_port) smtpPort = String(result.smtp_port);
				if (result.imap_host) imapHost = result.imap_host;
				if (result.imap_port) imapPort = String(result.imap_port);
				if (result.use_tls !== undefined) useTls = result.use_tls;
				providerNote = result.note ?? '';
			}
		} catch {
			// silent — user can fill manually
		} finally {
			detecting = false;
		}
	}

	function handleSubmit() {
		onSubmit({
			email,
			username: email,
			password,
			smtp_host: smtpHost,
			smtp_port: smtpPort,
			imap_host: imapHost,
			imap_port: imapPort,
			use_tls: String(useTls)
		});
	}

	const canSubmit = $derived(
		email.includes('@') && password && smtpHost && smtpPort && !isOAuthRedirect
	);
</script>

<div class="space-y-4">
	<!-- Email input with auto-detect -->
	<div>
		<label class="mb-1 block text-xs font-medium text-surface-400">Email Address</label>
		<input
			type="email"
			bind:value={email}
			onblur={detectProvider}
			placeholder="you@example.com"
			class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
		/>
		{#if detecting}
			<p class="mt-1 text-xs text-surface-500">Detecting provider settings...</p>
		{/if}
	</div>

	{#if isOAuthRedirect}
		<!-- OAuth redirect notice -->
		<div class="rounded-lg border border-laya-orange/30 bg-laya-orange/5 p-4">
			<p class="text-sm text-laya-orange">
				{detection?.provider ?? 'This provider'} uses OAuth for authentication.
			</p>
			<p class="mt-1 text-xs text-surface-400">
				{providerNote || 'Use the dedicated platform connection instead of SMTP.'}
			</p>
		</div>
	{:else}
		<!-- Provider note -->
		{#if providerNote}
			<div class="rounded-md border border-surface-600 bg-surface-800/50 px-3 py-2 text-xs text-surface-400">
				{#if detection?.provider}
					<span class="font-medium text-surface-300">{detection.provider}</span> —
				{/if}
				{providerNote}
			</div>
		{/if}

		<!-- Password / App Password -->
		<div>
			<label class="mb-1 block text-xs font-medium text-surface-400">Password / App Password</label>
			<input
				type="password"
				bind:value={password}
				placeholder="App password or account password"
				class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
			/>
		</div>

		<!-- SMTP settings -->
		<div class="grid grid-cols-3 gap-3">
			<div class="col-span-2">
				<label class="mb-1 block text-xs font-medium text-surface-400">SMTP Server</label>
				<input
					type="text"
					bind:value={smtpHost}
					placeholder="smtp.example.com"
					class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
				/>
			</div>
			<div>
				<label class="mb-1 block text-xs font-medium text-surface-400">Port</label>
				<input
					type="text"
					bind:value={smtpPort}
					placeholder="587"
					class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
				/>
			</div>
		</div>

		<!-- IMAP settings -->
		<div class="grid grid-cols-3 gap-3">
			<div class="col-span-2">
				<label class="mb-1 block text-xs font-medium text-surface-400">IMAP Server</label>
				<input
					type="text"
					bind:value={imapHost}
					placeholder="imap.example.com"
					class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
				/>
			</div>
			<div>
				<label class="mb-1 block text-xs font-medium text-surface-400">Port</label>
				<input
					type="text"
					bind:value={imapPort}
					placeholder="993"
					class="w-full rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
				/>
			</div>
		</div>

		<!-- TLS -->
		<label class="flex items-center gap-2 text-sm text-surface-300">
			<input type="checkbox" bind:checked={useTls} class="rounded border-surface-600" />
			Use TLS/STARTTLS
		</label>

		<!-- Submit -->
		<button
			onclick={handleSubmit}
			disabled={!canSubmit || submitting}
			class="w-full rounded-md bg-laya-orange px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-laya-gold disabled:opacity-50"
		>
			{submitting ? 'Connecting...' : 'Connect Email'}
		</button>
	{/if}
</div>
