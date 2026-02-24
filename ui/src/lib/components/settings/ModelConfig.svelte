<script lang="ts">
	import { onMount } from 'svelte';
	import { engineApi } from '$lib/api/engine';

	// Model options for each role
	const routerModels = [
		{ value: 'claude-haiku-4-5-20251001', label: 'Claude Haiku 4.5' },
		{ value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
		{ value: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash' },
		{ value: 'ollama/llama3', label: 'Ollama Llama 3 (local)' }
	];

	const stagerModels = [
		{ value: 'claude-sonnet-4-5-20250929', label: 'Claude Sonnet 4.5' },
		{ value: 'gpt-4o', label: 'GPT-4o' },
		{ value: 'gemini-2.0-pro', label: 'Gemini 2.0 Pro' },
		{ value: 'ollama/llama3', label: 'Ollama Llama 3 (local)' }
	];

	const providers = [
		{ id: 'anthropic', label: 'Anthropic', envVar: 'ANTHROPIC_API_KEY' },
		{ id: 'openai', label: 'OpenAI', envVar: 'OPENAI_API_KEY' },
		{ id: 'google', label: 'Google', envVar: 'GOOGLE_API_KEY' }
	];

	let models = $state({
		router: 'claude-haiku-4-5-20251001',
		stager: 'claude-sonnet-4-5-20250929',
		chat: 'claude-sonnet-4-5-20250929',
		local: 'ollama/llama3'
	});

	let apiKeys = $state<Record<string, boolean>>({
		anthropic: false,
		openai: false,
		google: false
	});

	let keyInputs = $state<Record<string, string>>({
		anthropic: '',
		openai: '',
		google: ''
	});

	let saving = $state(false);
	let savingKey = $state<string | null>(null);
	let loaded = $state(false);

	onMount(async () => {
		try {
			const settings = await engineApi.getSettings();
			models = { ...models, ...settings.models };
			apiKeys = { ...apiKeys, ...settings.api_keys };
			loaded = true;
		} catch (e) {
			console.error('Failed to load settings:', e);
		}
	});

	async function saveModels() {
		saving = true;
		try {
			await engineApi.updateSettings({ models } as any);
		} catch (e) {
			console.error('Failed to save models:', e);
		} finally {
			saving = false;
		}
	}

	async function saveApiKey(provider: string) {
		const key = keyInputs[provider];
		if (!key.trim()) return;

		savingKey = provider;
		try {
			await engineApi.setApiKey(provider, key.trim());
			apiKeys[provider] = true;
			keyInputs[provider] = '';
		} catch (e) {
			console.error('Failed to save API key:', e);
		} finally {
			savingKey = null;
		}
	}

	async function removeApiKey(provider: string) {
		try {
			await engineApi.deleteApiKey(provider);
			apiKeys[provider] = false;
		} catch (e) {
			console.error('Failed to remove API key:', e);
		}
	}
</script>

{#if !loaded}
	<div class="flex items-center justify-center py-12 text-surface-400">Loading settings...</div>
{:else}
	<div class="space-y-8">
		<!-- Model Selection -->
		<div class="rounded-lg border border-surface-700 bg-surface-800 p-5">
			<h3 class="mb-4 text-lg font-medium">Model Selection</h3>
			<div class="space-y-4">
				<div class="grid grid-cols-[140px_1fr] items-center gap-3">
					<label for="router-model" class="text-sm text-surface-400">Router (fast)</label>
					<select
						id="router-model"
						bind:value={models.router}
						onchange={saveModels}
						class="rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100"
					>
						{#each routerModels as model}
							<option value={model.value}>{model.label}</option>
						{/each}
					</select>
				</div>

				<div class="grid grid-cols-[140px_1fr] items-center gap-3">
					<label for="stager-model" class="text-sm text-surface-400">Stager (strong)</label>
					<select
						id="stager-model"
						bind:value={models.stager}
						onchange={saveModels}
						class="rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100"
					>
						{#each stagerModels as model}
							<option value={model.value}>{model.label}</option>
						{/each}
					</select>
				</div>

				<div class="grid grid-cols-[140px_1fr] items-center gap-3">
					<label for="chat-model" class="text-sm text-surface-400">Chat</label>
					<select
						id="chat-model"
						bind:value={models.chat}
						onchange={saveModels}
						class="rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100"
					>
						{#each stagerModels as model}
							<option value={model.value}>{model.label}</option>
						{/each}
					</select>
				</div>

				<div class="grid grid-cols-[140px_1fr] items-center gap-3">
					<label for="local-model" class="text-sm text-surface-400">Local (privacy)</label>
					<input
						id="local-model"
						type="text"
						bind:value={models.local}
						onchange={saveModels}
						placeholder="ollama/llama3"
						class="rounded-md border border-surface-600 bg-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500"
					/>
				</div>
			</div>
			{#if saving}
				<p class="mt-3 text-xs text-surface-400">Saving...</p>
			{/if}
		</div>

		<!-- API Keys -->
		<div class="rounded-lg border border-surface-700 bg-surface-800 p-5">
			<h3 class="mb-4 text-lg font-medium">API Keys</h3>
			<p class="mb-4 text-sm text-surface-400">
				Keys are stored securely in your OS keychain. They are never sent to the UI.
			</p>
			<div class="space-y-4">
				{#each providers as provider}
					<div class="flex items-center gap-3">
						<div class="flex w-24 items-center gap-2">
							<span
								class="h-2 w-2 rounded-full {apiKeys[provider.id]
									? 'bg-green-500'
									: 'bg-surface-500'}"
							></span>
							<span class="text-sm text-surface-300">{provider.label}</span>
						</div>

						{#if apiKeys[provider.id]}
							<span class="text-sm text-green-400">Configured</span>
							<button
								onclick={() => removeApiKey(provider.id)}
								class="ml-auto text-sm text-red-400 transition-colors hover:text-red-300"
							>
								Remove
							</button>
						{:else}
							<input
								type="password"
								bind:value={keyInputs[provider.id]}
								placeholder="Enter API key..."
								class="flex-1 rounded-md border border-surface-600 bg-surface-700 px-3 py-1.5 text-sm text-surface-100 placeholder:text-surface-500"
							/>
							<button
								onclick={() => saveApiKey(provider.id)}
								disabled={!keyInputs[provider.id].trim() || savingKey === provider.id}
								class="rounded-md bg-primary-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-primary-500 disabled:opacity-50"
							>
								{savingKey === provider.id ? 'Saving...' : 'Save'}
							</button>
						{/if}
					</div>
				{/each}
			</div>
		</div>
	</div>
{/if}
