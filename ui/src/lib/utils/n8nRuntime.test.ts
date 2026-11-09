// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest';
import {
	describeTarget,
	runtimeSummary,
	upgradeFailed,
	type N8nRuntimeRecord,
	type N8nRuntimeStatus,
} from './n8nRuntime';

const MANAGED = '/Users/u/.laya/node/bin/node';

function status(record: Partial<N8nRuntimeRecord> | null): N8nRuntimeStatus {
	return {
		record: record
			? {
					status: 'completed',
					n8n_version: '2.46.6',
					node_path: MANAGED,
					node_version: 'v24.22.2',
					expression_engine: 'vm',
					failed_target: null,
					retry_requested: false,
					attempt: null,
					...record,
				}
			: null,
		target: 'n8n-2.46.6+node-24.22.2',
		target_n8n_version: '2.46.6',
		target_node_version: '24.22.2',
		installed_n8n_version: '2.46.6',
		managed_node_path: MANAGED,
		upgrade_running: false,
	};
}

describe('upgradeFailed', () => {
	it('is true only for a failed record', () => {
		expect(upgradeFailed(status({ status: 'failed' }))).toBe(true);
		expect(upgradeFailed(status({ status: 'completed' }))).toBe(false);
		expect(upgradeFailed(status({ status: 'in_progress' }))).toBe(false);
		expect(upgradeFailed(status(null))).toBe(false);
		expect(upgradeFailed(null)).toBe(false);
	});
});

describe('runtimeSummary', () => {
	it('names a managed Node', () => {
		expect(runtimeSummary(status({}))).toBe(
			'n8n 2.46.6 on Node v24.22.2 (managed), expressions: vm'
		);
	});

	it('names a system Node and n8n default engine after a rollback', () => {
		expect(
			runtimeSummary(
				status({
					status: 'failed',
					n8n_version: '2.15.0',
					node_path: '/opt/homebrew/bin/node',
					node_version: 'v26.7.0',
					expression_engine: null,
				})
			)
		).toBe('n8n 2.15.0 on Node v26.7.0 (system), expressions: n8n default');
	});

	it('is null without a record', () => {
		expect(runtimeSummary(status(null))).toBeNull();
		expect(runtimeSummary(null)).toBeNull();
	});
});

describe('describeTarget', () => {
	it('splits a target identifier', () => {
		expect(describeTarget('n8n-2.46.6+node-24.22.2')).toBe('n8n 2.46.6 on Node 24.22.2');
	});

	it('returns other text unchanged', () => {
		expect(describeTarget('something else')).toBe('something else');
	});
});
