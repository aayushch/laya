<script lang="ts">
	import { page } from '$app/state';

	type TabId = 'terms' | 'license';
	let activeTab = $state<TabId>('terms');

	$effect(() => {
		const tab = page.url.searchParams.get('tab');
		if (tab === 'terms' || tab === 'license') {
			activeTab = tab;
		}
	});
</script>

<div class="mx-auto max-w-4xl px-6 py-8">
	<h1 class="mb-1 text-2xl font-bold text-surface-50">Legal</h1>
	<p class="mb-6 text-sm text-surface-400">Terms of use, privacy information, and open-source license</p>

	<!-- Tabs -->
	<div class="mb-8 flex gap-1 border-b border-surface-700">
		<button
			class="px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px
				{activeTab === 'terms'
					? 'border-laya-orange text-laya-orange'
					: 'border-transparent text-surface-400 hover:text-surface-200'}"
			onclick={() => (activeTab = 'terms')}
		>Terms &amp; Conditions</button>
		<button
			class="px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px
				{activeTab === 'license'
					? 'border-laya-orange text-laya-orange'
					: 'border-transparent text-surface-400 hover:text-surface-200'}"
			onclick={() => (activeTab = 'license')}
		>License</button>
	</div>

	{#if activeTab === 'terms'}
		<div class="prose-legal space-y-8 text-sm leading-relaxed text-surface-300">
			<p class="text-surface-500 text-xs">Last updated: April 2026</p>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">1. About Laya</h2>
				<p>
					Laya is an open-source, local-first desktop application that intercepts events from professional
					tools (such as Jira, Slack, Gmail, GitHub, Bitbucket, Google Calendar, Linear, and others),
					classifies them using large language models (LLMs), stages suggested actions, and presents
					them as Action Cards for your review and approval.
				</p>
				<p class="mt-2">
					Laya runs entirely on your machine. There are no Laya-operated servers, no accounts to create,
					and no telemetry or analytics data is transmitted to Laya's developers or any third party by the
					application itself.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">2. Data Processing &amp; Third-Party LLMs</h2>
				<p>
					<strong class="text-surface-100">This is the most important section to understand.</strong>
					To classify events, generate action suggestions, and power chat features, Laya sends portions
					of your professional data to the LLM provider you configure. This data may include:
				</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li>Email subjects and bodies (Gmail, Outlook)</li>
					<li>Slack/Discord messages and thread content</li>
					<li>Jira/Linear issue titles, descriptions, and comments</li>
					<li>GitHub/Bitbucket/GitLab pull request titles, descriptions, diffs, and review comments</li>
					<li>Calendar event titles and attendee information</li>
					<li>Your team member names and roles (as configured in Settings)</li>
				</ul>
				<div class="mt-4 rounded-lg border border-laya-orange/30 bg-laya-orange/5 p-4">
					<h3 class="mb-1 text-sm font-semibold text-laya-orange">Laya is Local-First</h3>
					<p class="text-surface-300">
						Laya is designed to keep your data on your machine. It fully supports self-hosted LLM
						providers including
						<strong class="text-surface-200">Ollama</strong>,
						<strong class="text-surface-200">LM Studio</strong>, and any
						<strong class="text-surface-200">OpenAI-compatible endpoint</strong>.
						When using self-hosted models, your data never leaves your machine or local network.
						<strong class="text-surface-200">This is the recommended configuration.</strong>
						You can configure self-hosted providers in <a href="/settings?tab=models" class="text-laya-orange underline underline-offset-2 hover:text-laya-gold">Settings &rarr; Models</a>.
					</p>
				</div>
				<p class="mt-3">
					However, Laya also supports cloud-hosted LLM providers such as
					<strong class="text-surface-200">Anthropic (Claude)</strong>,
					<strong class="text-surface-200">OpenAI (GPT)</strong>,
					<strong class="text-surface-200">Google (Gemini)</strong>, and
					<strong class="text-surface-200">OpenRouter</strong>.
					If you choose to configure a cloud provider, your data will be transmitted to their servers
					and will be subject to their respective privacy policies and terms of service. Laya has no
					control over how these third-party providers handle, store, or process your data.
					<strong class="text-surface-200">You should understand this risk before opting into a cloud-hosted provider.</strong>
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">3. Platform Integrations &amp; Outbound Actions</h2>
				<p>
					Laya connects to third-party platforms to ingest events and, with your explicit approval,
					execute actions on your behalf. These actions may include:
				</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li>Sending emails via Gmail or Outlook</li>
					<li>Posting messages or replies in Slack or Discord</li>
					<li>Creating or commenting on Jira, Linear, or GitHub issues</li>
					<li>Commenting on or approving pull requests</li>
					<li>Creating or modifying calendar events</li>
				</ul>
				<p class="mt-2">
					<strong class="text-surface-100">No action is ever executed without your explicit approval</strong>
					unless you have configured automatic execution for specific action types. You are solely
					responsible for reviewing and approving any actions Laya stages on your behalf. Laya provides
					suggested actions based on LLM output, which may contain errors, hallucinations, or
					inappropriate suggestions.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">4. Credential &amp; API Key Handling</h2>
				<p>
					Laya stores your API keys and OAuth tokens in your operating system's secure credential store
					(macOS Keychain, Windows Credential Manager, or Linux Secret Service). Credentials are
					<strong class="text-surface-200">never stored in plain text</strong> on disk.
				</p>
				<p class="mt-2">
					API keys are transmitted only to their respective service providers (e.g., your Anthropic API key
					is sent only to Anthropic's API). OAuth tokens are used to authenticate with the platforms you
					have connected. You can revoke any connection at any time from Settings.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">5. Local Data Storage</h2>
				<p>All Laya data is stored locally on your machine in the <code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">~/.laya/</code> directory:</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li><strong class="text-surface-200">SQLite database</strong> — events, action cards, chat history, audit logs, user feedback</li>
					<li><strong class="text-surface-200">ChromaDB vector store</strong> — semantic embeddings for search and entity resolution</li>
					<li><strong class="text-surface-200">Logs</strong> — application logs (rotated, 10 MB max, 5 files retained)</li>
					<li><strong class="text-surface-200">Configuration</strong> — settings, team definitions, rules, repository paths</li>
				</ul>
				<p class="mt-2">
					You own your data. You may delete the <code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">~/.laya/</code> directory at any time to remove all Laya data from your system. You can also manage and export your data from <a href="/settings?tab=data" class="text-laya-orange underline underline-offset-2 hover:text-laya-gold">Settings &rarr; Data</a>.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">6. Automation Engine (n8n)</h2>
				<p>
					Laya bundles <a href="https://n8n.io" target="_blank" rel="noopener noreferrer" class="text-laya-orange underline underline-offset-2 hover:text-laya-gold">n8n</a>,
					an open-source workflow automation tool, which runs as a local process on your machine (port 45678).
					n8n handles event ingestion from connected platforms and executes approved outbound actions.
					n8n's data and encrypted credentials are stored locally in <code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">~/.laya/n8n/</code>.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">7. Coding Agents</h2>
				<p>
					Laya can optionally integrate with coding agents (such as Claude Code, Gemini CLI, or OpenAI Codex CLI)
					to execute development tasks on your behalf. When enabled, these agents operate in your local
					development environment and may:
				</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li>Read and modify files in your configured repositories</li>
					<li>Execute shell commands on your machine</li>
					<li>Send code context to their respective LLM providers</li>
				</ul>
				<p class="mt-2">
					Agent actions require your approval before execution. You are responsible for reviewing
					agent-proposed changes before accepting them.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">8. No Telemetry or Analytics</h2>
				<p>
					Laya does not collect, transmit, or share any usage analytics, telemetry, crash reports,
					or diagnostic data with Laya's developers or any third party. All analytics visible in the
					Dashboard (event counts, cost estimates, response times) are computed and stored locally.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">9. Cost &amp; Billing</h2>
				<p>
					Laya itself is free and open-source. However, using cloud-hosted LLM providers incurs costs
					billed directly by those providers to your account. Laya provides cost estimation and budget
					controls in Settings, but these are approximations and do not constitute billing guarantees.
					You are responsible for monitoring your API provider usage and costs.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">10. Disclaimer of Warranties</h2>
				<p>
					LAYA IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
					NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND
					NON-INFRINGEMENT.
				</p>
				<p class="mt-2">
					LLM-generated suggestions, classifications, and staged actions may be inaccurate, incomplete,
					or inappropriate. You are solely responsible for reviewing all suggestions before approval.
					Laya's developers are not liable for any consequences arising from actions taken based on
					LLM-generated output, including but not limited to:
				</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li>Incorrect email replies or messages sent on your behalf</li>
					<li>Erroneous issue or pull request comments</li>
					<li>Miscategorized or misprioritized events</li>
					<li>Data unintentionally shared with LLM providers</li>
					<li>Unintended code modifications by coding agents</li>
				</ul>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">11. Limitation of Liability</h2>
				<p>
					IN NO EVENT SHALL THE AUTHORS, COPYRIGHT HOLDERS, OR CONTRIBUTORS OF LAYA BE LIABLE FOR
					ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING
					BUT NOT LIMITED TO PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES, LOSS OF USE, DATA, OR
					PROFITS, OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER
					IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY
					WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">12. Your Responsibilities</h2>
				<ul class="ml-4 list-disc space-y-1 text-surface-400">
					<li>Ensure you have the right to process data from your connected platforms through LLMs</li>
					<li>Comply with your organization's data handling and privacy policies</li>
					<li>Review the privacy policies of any cloud LLM providers you configure</li>
					<li>Review all staged actions before approving execution</li>
					<li>Secure your machine, as Laya stores sensitive data locally</li>
					<li>Comply with applicable laws regarding automated communications and data processing</li>
				</ul>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">13. Open Source</h2>
				<p>
					Laya is open-source software licensed under the Apache License 2.0. You may inspect, modify,
					and redistribute the source code subject to the license terms. See the
					<button class="text-laya-orange underline underline-offset-2 hover:text-laya-gold" onclick={() => (activeTab = 'license')}>License</button>
					tab for the full text.
				</p>
			</section>

			<section class="border-t border-surface-800 pt-6">
				<p class="text-xs text-surface-500">
					These terms may be updated from time to time. Changes will be reflected in updated versions
					of the application. Continued use of Laya constitutes acceptance of the current terms.
				</p>
			</section>
		</div>
	{:else}
		<div class="space-y-4">
			<div class="rounded-lg border border-surface-700 bg-surface-800/50 p-2 px-3">
				<p class="text-sm text-surface-300">
					Laya is licensed under the <strong class="text-surface-100">Apache License, Version 2.0</strong>.
					You may obtain a copy at
					<a href="https://www.apache.org/licenses/LICENSE-2.0" target="_blank" rel="noopener noreferrer" class="text-laya-orange underline underline-offset-2 hover:text-laya-gold">apache.org/licenses/LICENSE-2.0</a>.
				</p>
			</div>

			<pre class="max-h-[65vh] overflow-auto rounded-lg border border-surface-700 bg-surface-800/30 p-6 text-xs leading-relaxed text-surface-400">{`Copyright 2025-2026 Laya Contributors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

---

NOTICE: This product includes the following open-source components:

Engine (Python):
  - FastAPI (MIT)
  - LiteLLM (MIT)
  - ChromaDB (Apache 2.0)
  - aiosqlite (MIT)
  - httpx (BSD)
  - keyring (MIT)
  - Pydantic (MIT)
  - structlog (MIT/Apache 2.0)
  - tiktoken (MIT)
  - ONNX Runtime (MIT)
  - tenacity (Apache 2.0)
  - bcrypt (Apache 2.0)

Desktop Shell (Rust):
  - Tauri (MIT/Apache 2.0)
  - tokio (MIT)
  - reqwest (MIT/Apache 2.0)
  - serde (MIT/Apache 2.0)

Frontend (JavaScript/TypeScript):
  - Svelte & SvelteKit (MIT)
  - Skeleton UI (MIT)
  - Tailwind CSS (MIT)
  - marked (MIT)

Automation:
  - n8n (Sustainable Use License / Apache 2.0)

For the full license text of each dependency, please refer to
their respective repositories and package registries.`}</pre>
		</div>
	{/if}
</div>
