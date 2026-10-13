// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import { ENGINE_WS_PROTOCOL, engineWsProtocols } from './websocket';

describe('engineWsProtocols', () => {
	it('offers the protocol name first and the token second', () => {
		expect(engineWsProtocols('abc123')).toEqual([ENGINE_WS_PROTOCOL, 'abc123']);
	});
	it('uses the name the engine echoes back on accept', () => {
		expect(ENGINE_WS_PROTOCOL).toBe('laya.engine.v1');
	});
});
