import type { ActionCard } from '$lib/api/types';

function extractPlatform(card: ActionCard): string {
	if (card.entity_id) return card.entity_id.split(':')[0];
	return 'unknown';
}

export function buildCardContext(cards: ActionCard[]): string {
	const sections: string[] = [];
	sections.push(`The user is viewing ${cards.length} related card(s).`);
	sections.push('');

	for (const c of cards) {
		const lines = [
			`--- Card: ${c.card_id} ---`,
			`Title: ${c.header}`,
			`Summary: ${c.summary}`,
			`Priority: ${c.priority} | Status: ${c.status} | Persona: ${c.persona} | Category: ${c.category}`,
			`Platform: ${extractPlatform(c)}`
		];
		if (c.actor_name) lines.push(`Actor: ${c.actor_name}`);
		if (c.intelligence && c.intelligence.length > 0) {
			lines.push('Intelligence:');
			c.intelligence.forEach((p) => lines.push(`- ${p}`));
		}
		if (c.staged_output) {
			lines.push(`Staged Output (${c.staged_output.type}):`, c.staged_output.content);
		}
		if (c.source_ref) lines.push(`Source: ${c.source_ref}`);
		if (c.source_url) lines.push(`URL: ${c.source_url}`);
		lines.push('');
		sections.push(lines.join('\n'));
	}

	return sections.join('\n');
}

export function buildSingleCardContext(card: ActionCard): string {
	return buildCardContext([card]);
}
