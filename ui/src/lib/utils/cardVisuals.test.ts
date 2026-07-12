// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import {
	PRIORITY_LABELS,
	PRIORITY_COLORS,
	platformDotColor,
	platformKey,
	actorInitials,
	actorAvatarColor
} from './cardVisuals';

describe('priority maps', () => {
	it('abbreviates every priority label', () => {
		expect(PRIORITY_LABELS).toEqual({ CRITICAL: 'CRIT', HIGH: 'HIGH', MEDIUM: 'MED', LOW: 'LOW' });
	});
	it('has a colour class for every priority label key', () => {
		for (const k of Object.keys(PRIORITY_LABELS)) {
			expect(PRIORITY_COLORS[k]).toBeTruthy();
		}
	});
});

describe('platformDotColor', () => {
	it('maps a known platform to its brand colour', () => {
		expect(platformDotColor('gmail')).toBe('#EA4335');
		expect(platformDotColor('slack')).toBe('#611F69');
	});
	it('is case-insensitive', () => {
		expect(platformDotColor('GitHub')).toBe(platformDotColor('github'));
	});
	it('falls back to neutral grey for empty or unknown platforms', () => {
		expect(platformDotColor('')).toBe('#6B7280');
		expect(platformDotColor('myspace')).toBe('#6B7280');
	});
});

describe('platformKey', () => {
	it('extracts the platform from a namespaced entity id', () => {
		expect(platformKey('gmail:msg-123')).toBe('gmail');
	});
	it('lowercases and handles a bare platform token', () => {
		expect(platformKey('GitHub')).toBe('github');
	});
	it('returns an empty string for missing input', () => {
		expect(platformKey(undefined)).toBe('');
		expect(platformKey('')).toBe('');
	});
});

describe('actorInitials', () => {
	it('returns ? for missing or blank names', () => {
		expect(actorInitials(null)).toBe('?');
		expect(actorInitials(undefined)).toBe('?');
		expect(actorInitials('   ')).toBe('?');
	});
	it('takes first+last initials for a multi-word name', () => {
		expect(actorInitials('jane doe')).toBe('JD');
		expect(actorInitials('Mary Jane Watson')).toBe('MW');
	});
	it('takes the single initial for a one-word name', () => {
		expect(actorInitials('Madonna')).toBe('M');
	});
	it('strips a parenthetical suffix before extracting initials', () => {
		expect(actorInitials('Jane Doe (Jira)')).toBe('JD');
		// A name that is ONLY a parenthetical collapses to nothing → ?.
		expect(actorInitials('(Bitbucket)')).toBe('?');
	});
});

describe('actorAvatarColor', () => {
	it('returns a well-formed OKLCH colour', () => {
		expect(actorAvatarColor('Jane Doe')).toMatch(/^oklch\(0\.62 0\.11 \d+(\.\d+)?\)$/);
	});
	it('is deterministic — same name yields the same colour', () => {
		expect(actorAvatarColor('Jane Doe')).toBe(actorAvatarColor('Jane Doe'));
	});
	it('ignores case (hashes the lowercased name)', () => {
		expect(actorAvatarColor('JANE DOE')).toBe(actorAvatarColor('jane doe'));
	});
	it('falls back to a stable colour for a missing name', () => {
		expect(actorAvatarColor(null)).toBe(actorAvatarColor(undefined));
	});
	it('gives different names different hues (generally)', () => {
		expect(actorAvatarColor('Alice')).not.toBe(actorAvatarColor('Zebediah'));
	});
});
