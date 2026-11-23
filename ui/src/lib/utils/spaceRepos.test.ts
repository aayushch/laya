// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import { unresolvedRepoNames } from './spaceRepos';

describe('unresolvedRepoNames', () => {
	it('lists assigned names missing from the repo list, in assigned order', () => {
		expect(unresolvedRepoNames(['old-b', 'api', 'old-a'], [{ name: 'api' }, { name: 'web' }])).toEqual([
			'old-b',
			'old-a'
		]);
	});
	it('skips names that resolve to a repo', () => {
		expect(unresolvedRepoNames(['api', 'web'], [{ name: 'web' }, { name: 'api' }])).toEqual([]);
	});
	it('handles empty inputs', () => {
		expect(unresolvedRepoNames([], [{ name: 'api' }])).toEqual([]);
		expect(unresolvedRepoNames(['api'], [])).toEqual(['api']);
		expect(unresolvedRepoNames([], [])).toEqual([]);
	});
});
