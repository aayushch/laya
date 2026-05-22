// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

/**
 * Setup store — manages first-run environment setup state.
 *
 * On app launch (production), checks if the Python venv is ready.
 * If not, drives the setup flow with progress events from Tauri.
 */
import { writable } from 'svelte/store';

export interface EnvStatus {
	python_path: string | null;
	python_version: string | null;
	venv_ready: boolean;
	deps_installed: boolean;
	engine_source_found: boolean;
	node_found: boolean;
	n8n_installed: boolean;
}

export interface SetupStep {
	id: string;
	label: string;
	status: 'waiting' | 'running' | 'done' | 'warning' | 'error';
	message: string;
}

/** Whether the environment needs setup (null = not yet checked). */
export const needsSetup = writable<boolean | null>(null);

/** Current setup steps with their progress. */
export const setupSteps = writable<SetupStep[]>([
	{ id: 'preflight', label: 'Preflight checks', status: 'waiting', message: 'Checking prerequisites...' },
	{ id: 'environment', label: 'Setting up environment', status: 'waiting', message: 'Creating virtual environment...' },
	{ id: 'deps', label: 'Installing dependencies', status: 'waiting', message: 'Installing packages...' },
	{ id: 'automation', label: 'Setting up automation', status: 'waiting', message: 'Installing n8n...' },
	{ id: 'engine', label: 'Starting engine', status: 'waiting', message: 'Starting Laya engine...' },
]);

/** Error message if setup fails. */
export const setupError = writable<string | null>(null);

/** Whether setup has completed successfully. */
export const setupComplete = writable<boolean>(false);

/** Check if environment is ready. Call on app mount. */
export async function checkEnvironment(): Promise<EnvStatus | null> {
	try {
		const { invoke } = await import('@tauri-apps/api/core');
		const status = await invoke<EnvStatus>('check_environment');

		// If everything is ready, no setup needed
		if (status.venv_ready && status.deps_installed && status.engine_source_found) {
			needsSetup.set(false);
		} else {
			needsSetup.set(true);
		}

		return status;
	} catch {
		// Not running in Tauri (dev mode with npm run dev) — skip setup
		needsSetup.set(false);
		return null;
	}
}

/** Run the full setup flow. Listens for progress events from Tauri. */
export async function runSetup(): Promise<void> {
	setupError.set(null);

	try {
		const { invoke } = await import('@tauri-apps/api/core');
		const { listen } = await import('@tauri-apps/api/event');

		// Listen for progress events
		const unlisten = await listen<{ step: string; status: string; message: string }>(
			'setup-progress',
			(event) => {
				const { step, status, message } = event.payload;

				setupSteps.update((steps) =>
					steps.map((s) => {
						if (s.id === step) {
							return {
								...s,
								status: status as SetupStep['status'],
								message,
							};
						}
						return s;
					})
				);

				if (status === 'error') {
					setupError.set(message);
				}

				// If engine is done, setup is complete
				if (step === 'engine' && status === 'done') {
					setupComplete.set(true);
					needsSetup.set(false);
					unlisten();
				}
			}
		);

		// Trigger setup on the Rust side
		await invoke('setup_environment');
	} catch (e) {
		setupError.set(`Setup failed: ${e}`);
	}
}
