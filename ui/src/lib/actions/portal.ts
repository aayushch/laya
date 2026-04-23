export function portal(node: HTMLElement, target: HTMLElement = document.body) {
	target.appendChild(node);
	return {
		destroy() {
			node.remove();
		}
	};
}
