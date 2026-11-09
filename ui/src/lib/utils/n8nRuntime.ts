// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

/**
 * The n8n runtime record kept by the Tauri shell (`n8n_upgrade.rs`): which
 * n8n version is installed, which Node runs it, and the state of the last
 * upgrade attempt. Read by the banner after a failed upgrade and by the
 * n8n Process panel on the Status page.
 */

export interface N8nUpgradeAttempt {
	target: string;
	phase: string;
	started_at: string;
	finished_at: string | null;
	error: string | null;
}

export interface N8nRuntimeRecord {
	/** `completed`, `in_progress` or `failed`. */
	status: string;
	n8n_version: string;
	node_path: string;
	node_version: string;
	expression_engine: string | null;
	failed_target: string | null;
	retry_requested: boolean;
	attempt: N8nUpgradeAttempt | null;
}

/** The value returned by the `n8n_runtime_status` Tauri command. */
export interface N8nRuntimeStatus {
	record: N8nRuntimeRecord | null;
	target: string;
	target_n8n_version: string;
	target_node_version: string;
	installed_n8n_version: string | null;
	managed_node_path: string;
	upgrade_running: boolean;
}

/**
 * Call a Tauri command. Returns null in a plain browser, where the Tauri API
 * is not available, and rethrows errors the command itself returns.
 */
async function tauriInvoke<T>(cmd: string): Promise<T | null> {
	let invoke: (cmd: string) => Promise<T>;
	try {
		({ invoke } = await import('@tauri-apps/api/core'));
	} catch {
		return null;
	}
	if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return null;
	return invoke(cmd);
}

/** The runtime status, or null outside the desktop app. */
export async function fetchN8nRuntimeStatus(): Promise<N8nRuntimeStatus | null> {
	try {
		return await tauriInvoke<N8nRuntimeStatus>('n8n_runtime_status');
	} catch {
		return null;
	}
}

/** Ask the shell to retry the failed upgrade on the next launch. */
export async function requestN8nUpgradeRetry(): Promise<void> {
	await tauriInvoke<void>('request_n8n_upgrade_retry');
}

/** Withdraw a retry requested with `requestN8nUpgradeRetry`. */
export async function cancelN8nUpgradeRetry(): Promise<void> {
	await tauriInvoke<void>('cancel_n8n_upgrade_retry');
}

/** True when the last upgrade failed, so the banner should be shown. */
export function upgradeFailed(status: N8nRuntimeStatus | null): boolean {
	return status?.record?.status === 'failed';
}

/**
 * One line describing the running n8n for the Status page, for example
 * "n8n 2.46.6 on Node v24.22.2 (managed), expressions: vm". The Node is
 * "managed" when it is Laya's own copy in ~/.laya/node, and "system"
 * otherwise. When the record has no expression engine, n8n uses its default.
 */
export function runtimeSummary(status: N8nRuntimeStatus | null): string | null {
	const r = status?.record;
	if (!status || !r) return null;
	const source = r.node_path === status.managed_node_path ? 'managed' : 'system';
	const engine = r.expression_engine ?? 'n8n default';
	return `n8n ${r.n8n_version} on Node ${r.node_version} (${source}), expressions: ${engine}`;
}

/**
 * The n8n version and Node version in an upgrade target identifier such as
 * `n8n-2.46.6+node-24.22.2`, as "n8n 2.46.6 on Node 24.22.2". An identifier
 * in another format is returned unchanged.
 */
export function describeTarget(target: string): string {
	const m = /^n8n-(.+)\+node-(.+)$/.exec(target);
	return m ? `n8n ${m[1]} on Node ${m[2]}` : target;
}
