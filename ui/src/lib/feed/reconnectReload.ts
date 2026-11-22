// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

export function createReconnectReload(scheduleReload: () => void) {
	let connected = false;
	let hasConnected = false;
	let initialLoad: 'pending' | 'failed' | 'settled' = 'pending';

	function retryInitialLoad() {
		if (!connected || initialLoad !== 'failed') return;
		initialLoad = 'settled';
		scheduleReload();
	}

	return {
		connectionChanged(isConnected: boolean) {
			if (connected === isConnected) return;
			connected = isConnected;
			if (!connected) return;

			if (hasConnected) {
				initialLoad = 'settled';
				scheduleReload();
			} else {
				hasConnected = true;
				retryInitialLoad();
			}
		},
		loadFinished(succeeded: boolean) {
			if (initialLoad === 'settled') return;
			initialLoad = succeeded ? 'settled' : 'failed';
			retryInitialLoad();
		}
	};
}
