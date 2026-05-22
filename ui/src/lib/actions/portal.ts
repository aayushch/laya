// Copyright 2026 Aayush Chawla
// SPDX-License-Identifier: Apache-2.0

export function portal(node: HTMLElement, target: HTMLElement = document.body) {
	target.appendChild(node);
	return {
		destroy() {
			node.remove();
		}
	};
}
