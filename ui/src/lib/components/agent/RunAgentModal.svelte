<!-- Copyright 2026 Aayush Chawla -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
	import { agentDialog } from '$lib/stores/agentDialog';
	import { engineApi } from '$lib/api/engine';
	import { getEngineUrl, CODING_AGENTS } from '$lib/config';
	import { goto } from '$app/navigation';
	import { glassTheme } from '$lib/stores/glassTheme';
	import { portal } from '$lib/actions/portal';

	const AGENT_MODES: Record<string, string[]> = {
		claude_code: ['plan', 'acceptEdits'],
		codex_cli: ['read-only', 'full-auto']
	};

	const agents = CODING_AGENTS
		.filter((a) => a.value !== 'none')
		.map((a) => ({ value: a.value, label: a.label, modes: AGENT_MODES[a.value] ?? [] }));

	const modeLabels: Record<string, string> = {
		plan: 'Plan',
		acceptEdits: 'Accept Edits',
		'read-only': 'Read Only',
		'full-auto': 'Full Auto'
	};

	const modeDescriptions: Record<string, string> = {
		plan: 'Agent creates a plan and asks for approval before making changes',
		acceptEdits: 'Agent can read and write files without asking',
		'read-only': 'Agent can only read files, sandbox mode',
		'full-auto': 'Agent can read and write files automatically'
	};

	interface UploadedFile {
		path: string;
		filename: string;
		previewUrl: string | null;
		contentType: string;
		isImage: boolean;
	}

	import type { Repo } from '$lib/api/types';

	let selectedAgent = $state('claude_code');
	let selectedMode = $state('plan');
	let directory = $state('');
	let addDirs = $state<string[]>([]);
	let prompt = $state('');
	let files = $state<UploadedFile[]>([]);
	let submitting = $state(false);
	let uploading = $state(false);
	let error = $state<string | null>(null);
	let settingsLoaded = $state(false);
	let configuredAgent = $state<string | null>(null);
	let agentPaths = $state<Record<string, string>>({});
	let dragOver = $state(false);
	let repos = $state<Repo[]>([]);
	let dirDropdownOpen = $state(false);
	let dirTriggerRef = $state<HTMLButtonElement | null>(null);
	let dirDropPos = $state({ top: 0, left: 0, width: 0 });

	const currentAgent = $derived(agents.find((a) => a.value === selectedAgent));
	const availableModes = $derived(currentAgent?.modes ?? []);
	const hasMultipleModes = $derived(availableModes.length > 1);

	// Load settings to get configured agent and paths
	$effect(() => {
		if ($agentDialog.isOpen && !settingsLoaded) {
			loadSettings();
		}
	});

	// Reset mode when agent changes
	$effect(() => {
		const agent = agents.find((a) => a.value === selectedAgent);
		if (agent && agent.modes.length > 0) {
			if (!agent.modes.includes(selectedMode)) {
				selectedMode = agent.modes[0];
			}
		}
	});

	async function loadSettings() {
		try {
			const [settings, reposConfig] = await Promise.all([
				engineApi.getSettings(),
				engineApi.getRepos()
			]);
			const agent = settings.coding_agent || 'claude_code';
			if (agent !== 'none') {
				configuredAgent = agent;
				selectedAgent = agent;
			}
			agentPaths = settings.agent_paths || {};
			repos = reposConfig.repos || [];
			settingsLoaded = true;
		} catch {
			settingsLoaded = true;
		}
	}

	// --- Browse directory using Tauri dialog (graceful fallback for web dev) ---

	async function browseAddDir() {
		try {
			const { invoke } = await import('@tauri-apps/api/core');
			const path = await invoke<string>('pick_folder', {
				title: 'Select additional directory'
			});
			if (path && !addDirs.includes(path)) {
				addDirs = [...addDirs, path];
			}
		} catch {
			// Not in Tauri — ignore
		}
	}

	function removeAddDir(index: number) {
		addDirs = addDirs.filter((_, i) => i !== index);
	}

	// --- File upload/handling ---

	async function uploadFile(file: File) {
		uploading = true;
		error = null;
		try {
			const formData = new FormData();
			formData.append('file', file);

			const resp = await fetch('${getEngineUrl()}/upload-agent-file', {
				method: 'POST',
				body: formData
			});
			if (!resp.ok) {
				throw new Error(`Upload failed: ${resp.status}`);
			}
			const result = await resp.json();

			const contentType: string = result.content_type || file.type || '';
			const isImage = contentType.startsWith('image/');
			const previewUrl = isImage ? URL.createObjectURL(file) : null;
			files = [
				...files,
				{
					path: result.path,
					filename: result.filename,
					previewUrl,
					contentType,
					isImage
				}
			];
		} catch (err) {
			error = err instanceof Error ? err.message : 'File upload failed';
		} finally {
			uploading = false;
		}
	}

	// In Tauri on macOS, WKWebView does not emit HTML5 drop events for OS
	// file drops. We receive the paths from Tauri's native drag-drop event
	// and send them to a path-based upload endpoint; since the engine is
	// local, it reads from that path directly.
	async function uploadFileByPath(path: string) {
		uploading = true;
		error = null;
		try {
			const resp = await fetch('${getEngineUrl()}/upload-agent-file-path', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ path })
			});
			if (!resp.ok) {
				throw new Error(`Upload failed: ${resp.status}`);
			}
			const result = await resp.json();
			const contentType: string = result.content_type || '';
			const isImage = contentType.startsWith('image/');
			files = [
				...files,
				{
					path: result.path,
					filename: result.filename,
					previewUrl: null,
					contentType,
					isImage
				}
			];
		} catch (err) {
			error = err instanceof Error ? err.message : 'File upload failed';
		} finally {
			uploading = false;
		}
	}

	function deleteStagedFile(path: string) {
		// Fire-and-forget — UI doesn't need to wait, and the 24h sweep is a
		// backstop if this ever fails.
		fetch('${getEngineUrl()}/delete-agent-staging-file', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ path })
		}).catch(() => {
			// Ignore — sweep will clean up eventually.
		});
	}

	function removeFile(index: number) {
		const removed = files[index];
		if (removed.previewUrl) URL.revokeObjectURL(removed.previewUrl);
		deleteStagedFile(removed.path);
		files = files.filter((_, i) => i !== index);
	}

	let fileInput: HTMLInputElement | null = $state(null);

	function triggerFilePicker() {
		fileInput?.click();
	}

	function handleFilePickerChange(e: Event) {
		const target = e.target as HTMLInputElement;
		if (!target.files) return;
		for (const f of target.files) {
			uploadFile(f);
		}
		target.value = '';
	}

	function extOf(filename: string): string {
		const i = filename.lastIndexOf('.');
		if (i < 0 || i === filename.length - 1) return 'FILE';
		return filename.slice(i + 1).toUpperCase().slice(0, 4);
	}

	function handleDrop(e: DragEvent) {
		e.preventDefault();
		e.stopPropagation();
		dragOver = false;
		const dropped = e.dataTransfer?.files;
		if (dropped && dropped.length > 0) {
			for (const f of dropped) {
				uploadFile(f);
			}
		}
	}

	function handleDragOver(e: DragEvent) {
		e.preventDefault();
		e.stopPropagation();
		if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
		dragOver = true;
	}

	function handleDragLeave(e: DragEvent) {
		// Only clear when leaving the window, not when moving between child elements
		if (e.relatedTarget === null) dragOver = false;
	}

	// Window-level drag/drop listener while the modal is open — handles the
	// browser/Vite-dev path. In Tauri on macOS, these HTML5 events don't fire
	// for OS file drops, so we also subscribe to Tauri's native event below.
	$effect(() => {
		if (!$agentDialog.isOpen) return;
		window.addEventListener('dragover', handleDragOver);
		window.addEventListener('drop', handleDrop);
		window.addEventListener('dragleave', handleDragLeave);
		return () => {
			window.removeEventListener('dragover', handleDragOver);
			window.removeEventListener('drop', handleDrop);
			window.removeEventListener('dragleave', handleDragLeave);
		};
	});

	// Tauri native drag-drop — required for file drops on macOS WKWebView.
	$effect(() => {
		if (!$agentDialog.isOpen) return;
		let unlisten: (() => void) | undefined;
		(async () => {
			try {
				const { getCurrentWebviewWindow } = await import('@tauri-apps/api/webviewWindow');
				const w = getCurrentWebviewWindow();
				unlisten = await w.onDragDropEvent((event) => {
					const p = event.payload;
					if (p.type === 'enter' || p.type === 'over') {
						dragOver = true;
					} else if (p.type === 'drop') {
						dragOver = false;
						for (const path of p.paths) {
							uploadFileByPath(path);
						}
					} else {
						dragOver = false;
					}
				});
			} catch {
				// Not running inside Tauri (e.g. Vite dev in a browser) — fine,
				// HTML5 drop handlers above cover that case.
			}
		})();
		return () => {
			unlisten?.();
		};
	});

	function handlePaste(e: ClipboardEvent) {
		const items = e.clipboardData?.items;
		if (!items) return;
		for (const item of items) {
			if (item.kind === 'file') {
				e.preventDefault();
				const f = item.getAsFile();
				if (f) uploadFile(f);
			}
		}
	}

	// --- Submit ---

	async function submit() {
		if (!prompt.trim()) {
			error = 'Please enter a prompt';
			return;
		}

		submitting = true;
		error = null;

		try {
			const result = await engineApi.runAgent({
				prompt: prompt.trim(),
				directory: directory.trim() || undefined,
				add_dirs: addDirs.length > 0 ? addDirs : undefined,
				agent_type: selectedAgent,
				mode: availableModes.length > 0 ? selectedMode : undefined,
				files: files.length > 0 ? files.map((f) => f.path) : undefined
			});

			agentDialog.close();
			resetState();
			goto(`/workspace/${result.card_id}`);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to start agent';
		} finally {
			submitting = false;
		}
	}

	function resetState() {
		prompt = '';
		directory = '';
		addDirs = [];
		// Revoke preview URLs and delete any staged files that weren't submitted.
		// On successful submit the backend already moved the files out of staging,
		// so the delete endpoint will no-op for those paths (it's idempotent).
		for (const f of files) {
			if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
			deleteStagedFile(f.path);
		}
		files = [];
		error = null;
		submitting = false;
		uploading = false;
		settingsLoaded = false;
		dragOver = false;
		dirDropdownOpen = false;
	}

	function close() {
		agentDialog.close();
		resetState();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			if (dirDropdownOpen) { e.preventDefault(); dirDropdownOpen = false; return; }
		}
		if (e.key === '.' && e.metaKey) { e.preventDefault(); close(); return; }
		if (e.key === 'Enter' && e.metaKey) submit();
	}

	// Window-level listener: the backdrop div only receives keydown when focus
	// is inside it, but the modal typically opens with focus on the trigger
	// button outside. Listen on window while open so ESC always closes.
	$effect(() => {
		if (!$agentDialog.isOpen) return;
		window.addEventListener('keydown', handleKeydown);
		return () => window.removeEventListener('keydown', handleKeydown);
	});

	function handleBackdrop(e: MouseEvent) {
		// Intentionally no-op: modal only closes via close button or ⌘.
		// Backdrop kept as event sink so clicks outside the card don't bleed through.
	}

	// Close directory dropdown when clicking anywhere else
	function handleModalClick(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (dirDropdownOpen && !target.closest('[data-dir-dropdown]')) {
			dirDropdownOpen = false;
		}
	}

	const inputClass = $derived(
		$glassTheme
			? 'w-full rounded-md border border-surface-600/40 bg-surface-800/40 backdrop-blur-sm px-3 py-2 text-sm text-surface-200 placeholder-surface-600 focus:border-laya-orange/50 focus:outline-none focus:ring-1 focus:ring-laya-orange/30'
			: 'w-full rounded-md border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-surface-200 placeholder-surface-600 focus:border-laya-orange/50 focus:outline-none focus:ring-1 focus:ring-laya-orange/30'
	);
	const labelClass = 'block text-xs font-medium text-surface-400 mb-1';
</script>

{#if $agentDialog.isOpen}
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
		role="dialog"
		aria-label="Run Agent"
		tabindex="0"
		onclick={handleBackdrop}
		onkeydown={handleKeydown}
	>
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="mx-4 w-full max-w-2xl rounded-xl border max-h-[90vh] flex flex-col {$glassTheme ? 'glass-card border-surface-700/40' : 'border-surface-700 bg-surface-900 shadow-2xl'}"
			onclick={handleModalClick}
			onkeydown={handleKeydown}
		>
			<!-- Header -->
			<div
				class="flex items-center justify-between border-b px-5 py-3 shrink-0 {$glassTheme ? 'border-surface-700/40' : 'border-surface-700'}"
			>
				<div class="flex items-center gap-2">
					<svg
						class="h-4 w-4 text-laya-orange"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
						/>
					</svg>
					<h2 class="text-sm font-semibold text-surface-50">Run Agent</h2>
				</div>
				<button
					class="rounded p-1 text-surface-400 transition-colors hover:text-surface-200"
					onclick={close}
					aria-label="Close"
				>
					<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M6 18L18 6M6 6l12 12"
						/>
					</svg>
				</button>
			</div>

			<!-- Scrollable form body -->
			<div class="space-y-4 overflow-y-auto p-5">
				<!-- Agent selector -->
				<div>
					<span class={labelClass}>Agent</span>
					<div class="flex gap-2">
						{#each agents as agent}
							<button
								class="flex-1 rounded-lg border px-3 py-2 text-left transition-colors
									{selectedAgent === agent.value
									? 'border-laya-orange bg-laya-orange/10'
									: 'border-surface-600 bg-surface-800 hover:border-surface-500'}"
								onclick={() => (selectedAgent = agent.value)}
							>
								<div class="text-xs font-medium">{agent.label}</div>
								{#if agentPaths[agent.value]}
									<div class="mt-0.5 text-[10px] text-green-400/70">configured</div>
								{:else}
									<div class="mt-0.5 text-[10px] text-surface-500">not found</div>
								{/if}
							</button>
						{/each}
					</div>
				</div>

				<!-- Mode selector — always rendered for stable layout; disabled when agent has no modes -->
				<div class={hasMultipleModes ? '' : 'opacity-40 pointer-events-none'}>
					<span class={labelClass}>Mode</span>
					<div class="flex gap-2">
						{#if hasMultipleModes}
							{#each availableModes as mode}
								<button
									class="flex-1 rounded-lg border px-3 py-2 text-left transition-colors
										{selectedMode === mode
										? 'border-laya-orange bg-laya-orange/10'
										: 'border-surface-600 bg-surface-800 hover:border-surface-500'}"
									onclick={() => (selectedMode = mode)}
								>
									<div class="text-xs font-medium">{modeLabels[mode] || mode}</div>
									<div class="mt-0.5 text-[10px] text-surface-400 h-[2lh] line-clamp-2">
										{modeDescriptions[mode] || ''}
									</div>
								</button>
							{/each}
						{:else}
							<div class="flex-1 rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-left">
								<div class="text-xs font-medium text-surface-400">N/A</div>
								<div class="mt-0.5 text-[10px] text-surface-500 h-[2lh]">No mode options for this agent</div>
							</div>
							<div class="flex-1 rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-left">
								<div class="text-xs font-medium text-surface-400">&nbsp;</div>
								<div class="mt-0.5 text-[10px] text-surface-500 h-[2lh]">&nbsp;</div>
							</div>
						{/if}
					</div>
				</div>

				<!-- Working Directory -->
				<div class="relative" data-dir-dropdown>
					<label class={labelClass} for="agent-directory">Working Directory</label>
					<button
						bind:this={dirTriggerRef}
						id="agent-directory"
						type="button"
						class="flex w-full items-center justify-between rounded-md border border-surface-600 bg-surface-800 px-3 py-2 text-left text-sm transition-colors hover:border-surface-500 {directory ? 'text-surface-200' : 'text-surface-500'}"
						onclick={() => { if (!dirDropdownOpen && dirTriggerRef) { const r = dirTriggerRef.getBoundingClientRect(); dirDropPos = { top: r.bottom + 4, left: r.left, width: r.width }; } dirDropdownOpen = !dirDropdownOpen; }}
					>
						<span class="truncate">{directory || 'Optional — leave empty for a research workspace'}</span>
						<svg
							class="ml-2 h-4 w-4 shrink-0 text-surface-400 transition-transform {dirDropdownOpen ? 'rotate-180' : ''}"
							fill="none"
							stroke="currentColor"
							viewBox="0 0 24 24"
						>
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
						</svg>
					</button>

					{#if dirDropdownOpen}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div
							use:portal
							data-dir-dropdown
							class="fixed z-[100] rounded-md border {$glassTheme ? 'glass-dropdown border-white/15' : 'border-surface-600 bg-surface-800 shadow-lg'}"
							style="top: {dirDropPos.top}px; left: {dirDropPos.left}px; width: {dirDropPos.width}px;"
							onkeydown={(e) => { if (e.key === 'Escape') dirDropdownOpen = false; }}
						>
							<div class="max-h-48 overflow-y-auto py-1">
								{#if directory}
									<button
										class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-surface-400 transition-colors hover:bg-surface-700 hover:text-surface-200"
										onclick={() => { directory = ''; dirDropdownOpen = false; }}
									>
										<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
											<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
										</svg>
										Clear selection (use research workspace)
									</button>
								{/if}
								{#each repos as repo}
									<button
										class="flex w-full flex-col px-3 py-2 text-left transition-colors hover:bg-surface-700 {directory === repo.path ? 'bg-surface-700' : ''}"
										onclick={() => { directory = repo.path; dirDropdownOpen = false; }}
									>
										<span class="text-xs font-medium text-surface-200">{repo.name}</span>
										<span class="truncate text-[10px] text-surface-500">{repo.path}</span>
									</button>
								{/each}
								{#if repos.length === 0}
									<div class="px-3 py-2 text-xs text-surface-500">No repos configured</div>
								{/if}
							</div>
						</div>
					{/if}
					<p class="mt-1 text-[10px] text-surface-500">
						Leave empty to auto-provision a research workspace for this card.
					</p>
				</div>

				<!-- Additional directories -->
				<div>
					<label class={labelClass} for="agent-add-dirs-input">
						Additional Directories
						<span class="font-normal text-surface-500">(optional)</span>
					</label>
					{#if addDirs.length > 0}
						<div class="mb-2 space-y-1">
							{#each addDirs as dir, i}
								<div
									class="flex items-center gap-2 rounded-md border border-surface-700 bg-surface-800 px-3 py-1.5"
								>
									<span class="flex-1 truncate font-mono text-xs text-surface-300"
										>{dir}</span
									>
									<button
										class="shrink-0 text-surface-500 transition-colors hover:text-red-400"
										onclick={() => removeAddDir(i)}
										aria-label="Remove directory"
									>
										<svg
											class="h-3.5 w-3.5"
											fill="none"
											stroke="currentColor"
											viewBox="0 0 24 24"
										>
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												stroke-width="2"
												d="M6 18L18 6M6 6l12 12"
											/>
										</svg>
									</button>
								</div>
							{/each}
						</div>
					{/if}
					<button
						class="inline-flex items-center gap-1.5 rounded-md border border-dashed border-surface-600 px-3 py-1.5 text-xs text-surface-400 transition-colors hover:border-surface-500 hover:text-surface-300"
						onclick={browseAddDir}
					>
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M12 4v16m8-8H4"
							/>
						</svg>
						Add directory
					</button>
				</div>

				<!-- Prompt + file drop zone -->
				<div>
					<label class={labelClass} for="agent-prompt">Prompt</label>
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div
						class="relative"
						ondrop={handleDrop}
						ondragover={handleDragOver}
						ondragleave={handleDragLeave}
					>
						<textarea
							id="agent-prompt"
							bind:value={prompt}
							rows="6"
							class="{inputClass} resize-y {dragOver ? '!border-laya-orange !bg-laya-orange/5' : ''}"
							placeholder="Describe what you want the agent to do...&#10;&#10;Drop or paste files (PDFs, images, text) here for reference"
							onpaste={handlePaste}
						></textarea>

						{#if dragOver}
							<div
								class="pointer-events-none absolute inset-0 flex items-center justify-center rounded-md"
							>
								<div class="flex items-center gap-2 text-sm text-laya-orange">
									<svg
										class="h-5 w-5"
										fill="none"
										stroke="currentColor"
										viewBox="0 0 24 24"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
										/>
									</svg>
									Drop files here
								</div>
							</div>
						{/if}
					</div>

					<!-- File previews -->
					{#if files.length > 0}
						<div class="mt-2 flex flex-wrap gap-3">
							{#each files as f, i}
								<div class="group flex w-16 flex-col items-center">
									<div class="relative">
										{#if f.isImage && f.previewUrl}
											<img
												src={f.previewUrl}
												alt={f.filename}
												class="h-16 w-16 rounded-md border border-surface-700 object-cover"
											/>
										{:else}
											<div
												class="flex h-16 w-16 items-center justify-center rounded-md border border-surface-700 bg-surface-900"
												title={f.contentType}
											>
												<svg
													viewBox="0 0 40 48"
													class="h-12 w-10"
													xmlns="http://www.w3.org/2000/svg"
												>
													<path
														d="M4 4 H26 L36 14 V42 C 36 43.1 35.1 44 34 44 H6 C 4.9 44 4 43.1 4 42 Z"
														class="fill-surface-700"
													/>
													<path
														d="M26 4 L26 14 L36 14 Z"
														class="fill-surface-500"
														opacity="0.75"
													/>
													<rect
														x="4"
														y="32"
														width="32"
														height="10"
														class="fill-laya-orange"
														opacity="0.9"
													/>
													<text
														x="20"
														y="39.5"
														text-anchor="middle"
														class="fill-surface-900"
														font-size="7"
														font-weight="700"
														letter-spacing="0.5"
														>{extOf(f.filename)}</text
													>
												</svg>
											</div>
										{/if}
										<button
											class="absolute -right-1.5 -top-1.5 hidden rounded-full bg-surface-800 p-0.5 text-surface-400 shadow-md transition-colors hover:text-red-400 group-hover:block"
											onclick={() => removeFile(i)}
											aria-label="Remove file"
										>
											<svg
												class="h-3 w-3"
												fill="none"
												stroke="currentColor"
												viewBox="0 0 24 24"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													stroke-width="2"
													d="M6 18L18 6M6 6l12 12"
												/>
											</svg>
										</button>
									</div>
									<span
										class="mt-1 w-full truncate text-center text-[9px] text-surface-400"
										title={f.filename}
									>
										{f.filename}
									</span>
								</div>
							{/each}
						</div>
					{/if}

					{#if uploading}
						<div class="mt-1 flex items-center gap-1.5 text-[10px] text-surface-400">
							<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
								<circle
									class="opacity-25"
									cx="12"
									cy="12"
									r="10"
									stroke="currentColor"
									stroke-width="4"
								></circle>
								<path
									class="opacity-75"
									fill="currentColor"
									d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
								></path>
							</svg>
							Uploading file...
						</div>
					{/if}

					<div class="mt-3 flex items-center justify-between gap-2">
						<p class="text-[10px] text-surface-500">
							<kbd
								class="rounded border border-surface-600 bg-surface-800 px-1 py-0.5 font-mono text-[10px]"
								>Cmd+Enter</kbd
							> to submit &middot; drop, paste, or
							<button
								type="button"
								class="text-laya-orange underline-offset-2 hover:underline"
								onclick={triggerFilePicker}
							>browse files</button>
						</p>
					</div>
					<input
						bind:this={fileInput}
						type="file"
						multiple
						class="hidden"
						onchange={handleFilePickerChange}
					/>
				</div>

				{#if error}
					<p class="text-xs text-red-400">{error}</p>
				{/if}
			</div>

			<!-- Footer -->
			<div
				class="flex items-center justify-end gap-2 border-t px-5 py-3 shrink-0 {$glassTheme ? 'border-surface-700/40' : 'border-surface-700'}"
			>
				<button
					class="rounded-md px-3 py-1.5 text-xs text-surface-400 transition-colors hover:text-surface-200"
					onclick={close}
					disabled={submitting}
				>
					Cancel
				</button>
				<button
					class="inline-flex items-center gap-1.5 rounded-md bg-laya-orange/20 px-4 py-1.5 text-xs font-medium text-laya-orange transition-colors hover:bg-laya-orange/30 disabled:cursor-not-allowed disabled:opacity-50"
					onclick={submit}
					disabled={submitting || !prompt.trim()}
				>
					{#if submitting}
						<svg class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
							<circle
								class="opacity-25"
								cx="12"
								cy="12"
								r="10"
								stroke="currentColor"
								stroke-width="4"
							></circle>
							<path
								class="opacity-75"
								fill="currentColor"
								d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
							></path>
						</svg>
						Starting...
					{:else}
						<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"
							/>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
							/>
						</svg>
						Run Agent
					{/if}
				</button>
			</div>
			<p class="px-5 pb-3 pt-1.5 w-full text-right text-[10px] text-surface-500">Press <kbd class="rounded border border-surface-600 px-1 py-0.5 font-mono text-surface-400">⌘.</kbd> to close</p>
		</div>
	</div>
{/if}
