<script lang="ts">
	import { page } from '$app/state';

	type TabId = 'terms' | 'license' | 'privacy';
	let activeTab = $state<TabId>('terms');

	$effect(() => {
		const tab = page.url.searchParams.get('tab');
		if (tab === 'terms' || tab === 'license' || tab === 'privacy') {
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
		<button
			class="px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px
				{activeTab === 'privacy'
					? 'border-laya-orange text-laya-orange'
					: 'border-transparent text-surface-400 hover:text-surface-200'}"
			onclick={() => (activeTab = 'privacy')}
		>Privacy Policy</button>
	</div>

	{#if activeTab === 'terms'}
		<div class="prose-legal space-y-8 text-sm leading-relaxed text-surface-300">
			<p class="text-surface-500 text-xs">Last updated: April 2026</p>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">1. About Laya</h2>
				<p>
					Laya is an open-source, local-first desktop application that intercepts events from
					professional tools (such as Jira, Slack, Gmail, Outlook, GitHub, Bitbucket, GitLab,
					Google Calendar, Linear, Notion, and others you may connect), classifies them using
					large language models (LLMs), stages suggested actions, and presents them as Action
					Cards for your review and approval.
				</p>
				<p class="mt-2">
					Laya runs entirely on your machine. There are no Laya-operated servers, no Laya
					accounts to create, and no telemetry or analytics data is transmitted to Laya's
					developers or any third party by the application itself. Laya's developers do not
					operate any email, messaging, code-hosting, calendar, or other platform accounts on
					your behalf — every ingestion connection and every outbound action uses credentials
					you personally provide and operates under your own identity on each platform.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">2. Data Processing &amp; Third-Party LLMs</h2>
				<p>
					<strong class="text-surface-100">This is the most important section to understand.</strong>
					To classify events, generate action suggestions, power chat, and compute semantic
					embeddings, Laya sends portions of your professional data to the LLM and embedding
					providers you configure. The specific data transmitted on any given request depends on
					the event type, the persona processing it, and the prompts Laya uses, but may include:
				</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li>Email subjects, bodies, and attachment metadata from your configured email providers (such as Gmail, Outlook, and any other email provider you connect)</li>
					<li>Messages, thread content, reactions, and channel metadata from your configured messaging platforms (such as Slack, Discord, Microsoft Teams, and other messaging platforms you connect)</li>
					<li>Issue titles, descriptions, comments, labels, and status metadata from your configured project- or task-management tools (such as Jira, Linear, Notion, Asana, and other tools you connect)</li>
					<li>Pull or merge request titles, descriptions, code diffs, review comments, and file paths from your configured code-hosting platforms (such as GitHub, Bitbucket, GitLab, and other code-hosting platforms you connect)</li>
					<li>Calendar event titles, descriptions, attendee information, and schedule metadata from your configured calendar providers (such as Google Calendar, Outlook Calendar, and other calendar providers you connect)</li>
					<li>Page or document titles, content, and metadata from your configured knowledge bases (such as Notion, Confluence, and others)</li>
					<li>Your team member names, roles, and relationships as configured in Settings</li>
					<li>Any prompts, files, or context you submit through Laya's chat or workspace interfaces</li>
					<li>Any additional data surfaced by custom workflows you build or modify in the bundled n8n automation engine</li>
				</ul>
				<p class="mt-3 text-surface-400">
					The list above is illustrative and not exhaustive. The exact fields sent to an LLM
					provider on any given request are determined by the prompts and pipeline of the
					installed version of Laya and may change between releases. You should assume that any
					content ingested from a connected platform is eligible to be transmitted to your
					configured LLM provider.
				</p>
				<div class="mt-4 rounded-lg border border-laya-orange/30 bg-laya-orange/5 p-4">
					<h3 class="mb-1 text-sm font-semibold text-laya-orange">Laya is Local-First</h3>
					<p class="text-surface-300">
						Laya is designed to keep your data on your machine. It fully supports self-hosted LLM
						providers including
						<strong class="text-surface-200">Ollama</strong>,
						<strong class="text-surface-200">LM Studio</strong>, and any
						<strong class="text-surface-200">OpenAI-compatible endpoint</strong>.
						When using self-hosted models, your data does not leave your machine or local network via Laya.
						<strong class="text-surface-200">This is the recommended configuration.</strong>
						You can configure self-hosted providers in <a href="/settings?tab=models" class="text-laya-orange underline underline-offset-2 hover:text-laya-gold">Settings &rarr; Models</a>.
					</p>
				</div>
				<p class="mt-3">
					Laya also supports cloud-hosted LLM providers such as
					<strong class="text-surface-200">Anthropic (Claude)</strong>,
					<strong class="text-surface-200">OpenAI (GPT)</strong>,
					<strong class="text-surface-200">Google (Gemini)</strong>,
					<strong class="text-surface-200">OpenRouter</strong>, and any other OpenAI-compatible
					provider you configure. If you configure a cloud provider, the data described above
					will be transmitted to that provider's servers and will be subject to that provider's
					privacy policy, data-processing agreement, and terms of service.
				</p>
				<p class="mt-3">
					Different LLM providers handle your data differently. Some retain inputs for abuse
					monitoring, some offer zero-retention or enterprise endpoints, and some may — under
					default or consumer plans — use your inputs to train or improve their models. Laya has
					no control over, visibility into, or ability to enforce the data-handling practices of
					any LLM provider.
					<strong class="text-surface-200">Before configuring a cloud-hosted provider, you are responsible for reviewing that provider's current terms and selecting a plan, tier, or endpoint appropriate for the sensitivity of your data.</strong>
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">3. Platform Integrations &amp; Outbound Actions</h2>
				<p>
					Laya connects to third-party platforms to ingest events and, with your approval,
					execute actions on your behalf.
					<strong class="text-surface-100">All outbound actions are executed exclusively through the connections you personally authorize</strong>
					(via OAuth or API keys you provide) and are attributed to your own account on the
					destination platform. Laya does not operate or share any accounts, workspaces,
					mailboxes, or identities of its own. Example actions include:
				</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li>Sending, replying to, or forwarding email through your configured email providers (such as Gmail, Outlook, and any other email provider you connect)</li>
					<li>Posting messages, replies, or reactions through your configured messaging platforms (such as Slack, Discord, Microsoft Teams, and others)</li>
					<li>Creating, updating, or commenting on issues through your configured project- or task-management tools (such as Jira, Linear, GitHub Issues, Notion, and others)</li>
					<li>Commenting on, approving, requesting changes to, or merging pull or merge requests through your configured code-hosting platforms (such as GitHub, Bitbucket, GitLab, and others)</li>
					<li>Creating, updating, or cancelling events through your configured calendar providers (such as Google Calendar, Outlook Calendar, and others)</li>
					<li>Any additional action enabled by workflows you configure in the bundled n8n automation engine</li>
				</ul>
				<p class="mt-2">
					The list above is illustrative and not exhaustive; the set of supported platforms and
					actions evolves with each release of Laya. You are solely responsible for the content,
					timing, recipients, and consequences of every outbound action executed through your
					connected accounts, regardless of whether that action was originally suggested by an
					LLM, triggered by a workflow, or produced by a coding agent.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">4. Manual Approval &amp; Automatic Execution</h2>
				<p>
					<strong class="text-surface-100">No action is executed without your explicit approval</strong>
					unless you have opted into automatic execution for a specific action type, workflow,
					or rule. LLM-suggested actions may contain errors, hallucinations, factual
					inaccuracies, incorrect recipients, or otherwise inappropriate content; you are solely
					responsible for reviewing every staged action before approval.
				</p>
				<p class="mt-2">
					If you enable automatic execution, Laya will carry out those actions on your behalf
					without per-action confirmation. Automatic execution amplifies the consequences of any
					LLM error, misclassification, or prompt-injection attempt originating in ingested
					content. You accept full responsibility for all actions performed under automatic
					execution rules you configure, and for the rules themselves.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">5. Credential &amp; API Key Handling</h2>
				<p>
					Laya stores your API keys and OAuth tokens in your operating system's secure credential store
					(macOS Keychain, Windows Credential Manager, or Linux Secret Service). Credentials are
					<strong class="text-surface-200">never stored in plain text</strong> on disk by Laya.
				</p>
				<p class="mt-2">
					API keys are transmitted only to their respective service providers (for example, your
					Anthropic API key is sent only to Anthropic's API). OAuth tokens are used to
					authenticate with the platforms you have connected. You can revoke any connection at
					any time from Settings, and you are responsible for revoking credentials on the
					originating platform as well if you wish to fully terminate Laya's access.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">6. Local Data Storage &amp; Environment Security</h2>
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
				<p class="mt-2">
					Because Laya stores sensitive event content, credentials, and n8n workflow data on
					disk, the security of that data ultimately depends on the security of your machine.
					You are responsible for maintaining full-disk encryption, operating-system updates,
					user-account access controls, endpoint protection, and physical security for any
					device on which you run Laya. Laya's developers make no representations about the
					security of the environment in which the application runs.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">7. Automation Engine (n8n)</h2>
				<p>
					Laya bundles <a href="https://n8n.io" target="_blank" rel="noopener noreferrer" class="text-laya-orange underline underline-offset-2 hover:text-laya-gold">n8n</a>,
					an open-source workflow automation tool, which runs as a local process on your machine (port 45678).
					n8n handles event ingestion from connected platforms and executes approved outbound actions.
					n8n's data and encrypted credentials are stored locally in <code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">~/.laya/n8n/</code>.
				</p>
				<p class="mt-2">
					You may create, modify, import, or disable n8n workflows beyond those bundled with
					Laya. Any custom workflows you configure are your own responsibility, including the
					data they ingest, the actions they execute, the credentials they use, and their
					compliance with the originating platforms' terms. n8n is distributed under its own
					licenses and is not a product of Laya.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">8. Coding Agents</h2>
				<p>
					Laya can optionally integrate with coding agents (such as Claude Code, Gemini CLI, or OpenAI Codex CLI)
					to execute development tasks on your behalf. When enabled, these agents operate in your local
					development environment and may:
				</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li>Read and modify files within your configured repositories and any directories to which they are granted access</li>
					<li>Execute arbitrary shell commands on your machine with the privileges of your user account</li>
					<li>Install dependencies, run build scripts, run tests, and invoke other tooling on your system</li>
					<li>Transmit source code, file contents, and command output to their respective LLM providers for reasoning</li>
				</ul>
				<p class="mt-2">
					Coding agents carry significant risk. A misinterpreted instruction, erroneous LLM
					output, or prompt-injection attempt can result in deleted or corrupted files,
					credential exposure, unintended git history rewrites, or unwanted changes to your
					environment. Agent actions require your approval before execution unless you
					explicitly opt into autonomous operation. You are solely responsible for reviewing
					agent-proposed changes, sandboxing agent environments appropriately, maintaining
					backups, and understanding the terms and limitations of the underlying agent tools,
					which are not part of Laya.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">9. Telemetry &amp; Analytics</h2>
				<p>
					<strong class="text-surface-100">Laya itself does not collect, transmit, or share</strong>
					any usage analytics, telemetry, crash reports, or diagnostic data with Laya's
					developers or any third party. All analytics visible in the Dashboard (event counts,
					cost estimates, response times) are computed and stored locally on your machine.
				</p>
				<p class="mt-3">
					Laya bundles several open-source third-party components (including but not limited to
					<strong class="text-surface-200">ChromaDB</strong>, <strong class="text-surface-200">n8n</strong>,
					<strong class="text-surface-200">sentence-transformers</strong>, and the
					<strong class="text-surface-200">HuggingFace Hub</strong> client). Some of these
					components emit anonymous telemetry to their respective maintainers by default.
					<strong class="text-surface-200">Laya makes a best-effort attempt to disable telemetry in every bundled third-party component that offers an opt-out</strong>
					by applying the relevant configuration flags and environment variables at application
					startup — for example, ChromaDB's <code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">anonymized_telemetry</code>
					setting, n8n's <code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">N8N_DIAGNOSTICS_ENABLED</code>
					family of variables, HuggingFace's <code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">HF_HUB_DISABLE_TELEMETRY</code>,
					and the generic <code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">DO_NOT_TRACK</code> signal.
				</p>
				<p class="mt-3">
					However, this is inherently best-effort and cannot be guaranteed. A bundled
					third-party component may, without notice to Laya's developers:
				</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li>Rename, remove, or ignore its telemetry opt-out in a future version</li>
					<li>Silently re-enable telemetry when upgraded to a newer release bundled with Laya</li>
					<li>Emit telemetry that has no documented or supported opt-out mechanism</li>
					<li>Be replaced by, or joined by, a new dependency that has mandatory telemetry</li>
				</ul>
				<p class="mt-3">
					If you require an absolute guarantee that no network traffic leaves your machine
					beyond what you explicitly authorize, we recommend running Laya on a machine with an
					outbound firewall, DNS filtering, or equivalent controls, and independently inspecting
					the network activity of the <code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">laya-engine</code>
					process, the bundled <code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">node</code>
					(n8n) process, and any LLM or embedding providers you configure.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">10. Cost &amp; Billing</h2>
				<p>
					Laya itself is free and open-source. However, using cloud-hosted LLM, embedding, or
					platform providers incurs costs billed directly by those providers to your account.
					Laya provides cost estimation and budget controls in Settings, but these are
					approximations derived from local token counting and published provider pricing, and
					do not constitute billing guarantees. You are solely responsible for monitoring your
					provider usage, quotas, and costs, and for any overages or unexpected charges.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">11. Compliance With Third-Party Platforms</h2>
				<p>
					You are responsible for complying with the terms of service, acceptable-use policies,
					data-processing agreements, and API rate limits of every platform you connect to Laya
					(including but not limited to email providers, messaging platforms, project-management
					tools, code-hosting platforms, calendar providers, knowledge bases, and LLM or
					embedding providers). Laya does not monitor or enforce these policies on your behalf.
				</p>
				<p class="mt-2">
					If a connected platform suspends, rate-limits, or otherwise restricts your account due
					to activity originating from Laya — including automated ingestion, outbound actions,
					or LLM-generated content — you assume full responsibility for the consequences.
				</p>
				<p class="mt-2">
					You are also responsible for ensuring that your use of Laya with a given platform is
					permitted by your employer, organization, or any applicable data-sharing,
					confidentiality, or regulatory agreement covering the content on that platform.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">12. Your Responsibilities</h2>
				<ul class="ml-4 list-disc space-y-1 text-surface-400">
					<li>Ensure you have the right to process data from your connected platforms through LLMs and embedding providers</li>
					<li>Comply with your organization's data-handling, privacy, and acceptable-use policies</li>
					<li>Review the privacy policies and data-processing terms of any LLM, embedding, or platform provider you configure</li>
					<li>Review all staged actions before approving execution, and carefully consider the implications of enabling automatic execution</li>
					<li>Secure your machine, as Laya stores sensitive data locally</li>
					<li>Comply with applicable laws regarding automated communications, data processing, privacy, and export controls</li>
					<li>Maintain appropriate backups of any repositories, mailboxes, or data you cannot afford to lose</li>
				</ul>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">13. Disclaimer of Warranties</h2>
				<p>
					LAYA IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
					IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
					PARTICULAR PURPOSE, NON-INFRINGEMENT, ACCURACY, AND UNINTERRUPTED OPERATION.
				</p>
				<p class="mt-2">
					LLM-generated suggestions, classifications, summaries, and staged actions may be
					inaccurate, incomplete, biased, or inappropriate. You are solely responsible for
					reviewing all suggestions before approval. Laya's developers are not liable for any
					consequences arising from actions taken based on LLM-generated output, including but
					not limited to:
				</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li>Incorrect, missent, or inappropriate emails, messages, or replies sent on your behalf</li>
					<li>Erroneous issue, document, or pull request comments, reviews, or approvals</li>
					<li>Miscategorized, misprioritized, or missed events</li>
					<li>Data unintentionally shared with LLM, embedding, or platform providers</li>
					<li>Unintended code modifications, file deletions, or shell-command effects from coding agents</li>
					<li>Financial loss from unexpected LLM or platform API usage</li>
					<li>Disputes arising from automated or automatically-executed actions</li>
				</ul>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">14. Limitation of Liability</h2>
				<p>
					IN NO EVENT SHALL THE AUTHORS, COPYRIGHT HOLDERS, CONTRIBUTORS, OR MAINTAINERS OF LAYA
					BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
					DAMAGES (INCLUDING BUT NOT LIMITED TO PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES,
					LOSS OF USE, DATA, OR PROFITS, BUSINESS INTERRUPTION, REPUTATIONAL HARM, OR REGULATORY
					PENALTIES) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
					LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
					USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">15. Indemnification</h2>
				<p>
					To the fullest extent permitted by applicable law, you agree to indemnify, defend, and
					hold harmless Laya's authors, copyright holders, contributors, and maintainers from
					and against any claim, demand, loss, liability, damage, fine, penalty, or expense
					(including reasonable attorneys' fees) arising out of or related to:
				</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li>Your use of, or inability to use, Laya</li>
					<li>Your violation of these terms or of any applicable law, regulation, or third-party right</li>
					<li>Your violation of any third-party platform's terms of service, acceptable-use policy, or data-processing agreement</li>
					<li>Any content ingested, generated, sent, posted, modified, or deleted through your connected accounts via Laya, whether approved manually, executed automatically, or produced by a coding agent</li>
					<li>Any dispute between you and a third party (including your employer, colleagues, customers, or connected platforms) relating to activity carried out through Laya</li>
				</ul>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">16. Age, Authority &amp; Acceptance</h2>
				<p>
					By installing, configuring, or using Laya, you represent that you are at least the age
					of majority in your jurisdiction and that you have the legal capacity to accept these
					terms. If you are using Laya on behalf of an employer or organization, you further
					represent that you have the authority to bind that organization to these terms and to
					authorize the data processing, outbound actions, and credential usage described above.
				</p>
				<p class="mt-2">
					If you do not agree with any part of these terms, you must not install or use Laya.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">17. Software Updates &amp; Changes to These Terms</h2>
				<p>
					Laya may be updated from time to time. Updates may modify data formats, adjust
					pipeline or prompt behavior (which can change classification outcomes, action
					suggestions, or the specific content sent to LLM providers), add or remove supported
					platforms, and revise these terms. The "Last updated" date at the top of this page
					reflects the currently-effective version.
				</p>
				<p class="mt-2">
					You are responsible for keeping your installation of Laya reasonably up to date in
					order to receive security fixes. Continued use of Laya after an update constitutes
					acceptance of the then-current terms.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">18. Severability &amp; Entire Agreement</h2>
				<p>
					If any provision of these terms is found by a court of competent jurisdiction to be
					invalid, illegal, or unenforceable, the remaining provisions will continue in full
					force and effect, and the unenforceable provision will be modified only to the extent
					necessary to make it enforceable while preserving its original intent.
				</p>
				<p class="mt-2">
					These terms, together with the Apache License 2.0 under which Laya is distributed,
					constitute the entire agreement between you and Laya's contributors regarding your use
					of the software and supersede any prior understandings or communications on the
					subject. Except where superseded by mandatory local law, these terms are interpreted
					in accordance with the terms of the Apache License 2.0 and generally-accepted
					principles of open-source software law.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">19. Open Source</h2>
				<p>
					Laya is open-source software licensed under the Apache License 2.0. You may inspect, modify,
					and redistribute the source code subject to the license terms. See the
					<button class="text-laya-orange underline underline-offset-2 hover:text-laya-gold" onclick={() => (activeTab = 'license')}>License</button>
					tab for the full text.
				</p>
			</section>
		</div>
	{:else if activeTab === 'privacy'}
		<div class="prose-legal space-y-8 text-sm leading-relaxed text-surface-300">
			<p class="text-surface-500 text-xs">Last updated: May 2026</p>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">1. Who We Are</h2>
				<p>
					Laya is an open-source desktop application distributed under the Apache License 2.0.
					There is no company, hosted service, or data controller behind Laya. The software runs
					entirely on your machine. Laya's contributors do not operate servers, collect
					registrations, or maintain accounts on your behalf.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">2. Data We Collect</h2>
				<p>
					<strong class="text-surface-200">Laya itself collects no personal data from you.</strong>
					There are no Laya-operated servers to receive data, no analytics endpoints, no crash
					reporters, and no telemetry transmitted to Laya's contributors.
				</p>
				<p class="mt-2">
					All data that Laya processes — events from connected platforms, action cards, chat
					messages, embeddings, settings, and logs — is stored locally on your machine in the
					<code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">~/.laya/</code>
					directory. This includes:
				</p>
				<ul class="mt-2 ml-4 list-disc space-y-1 text-surface-400">
					<li>SQLite database (events, action cards, chat history, audit logs, feedback)</li>
					<li>ChromaDB vector store (semantic embeddings for search)</li>
					<li>Application logs (rotated, 10 MB max, 5 files retained)</li>
					<li>Configuration files (settings, team definitions, rules)</li>
				</ul>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">3. Data Sent to Third-Party LLM Providers</h2>
				<p>
					To classify events, generate action suggestions, power chat, and compute semantic
					embeddings, Laya transmits portions of your professional data to the LLM and embedding
					providers you configure. The data transmitted may include email content, messages, issue
					descriptions, PR diffs, calendar details, team member information, and any content you
					enter in chat or workspace interfaces.
				</p>
				<p class="mt-2">
					<strong class="text-surface-200">You choose which providers receive your data.</strong>
					Laya supports fully local models (Ollama, LM Studio, any OpenAI-compatible local
					endpoint) where no data leaves your machine. If you configure cloud providers
					(Anthropic, OpenAI, Google, OpenRouter), your data is subject to those providers' privacy
					policies and data-processing terms. Review their policies before configuring them.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">4. Platform Integrations</h2>
				<p>
					Laya connects to third-party platforms (Gmail, Slack, Jira, GitHub, Bitbucket, Calendar,
					Outlook, Linear, Notion, and others) using credentials you provide (OAuth tokens or API
					keys). These connections operate under your identity on each platform. Laya ingests
					events from these platforms and, with your approval, can execute actions on your behalf.
				</p>
				<p class="mt-2">
					Credentials are stored in your operating system's secure credential store (macOS
					Keychain, Windows Credential Manager, or Linux Secret Service) and are never stored in
					plain text by Laya.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">5. MCP Server</h2>
				<p>
					Laya exposes a Model Context Protocol (MCP) server on
					<code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">http://127.0.0.1:8420/mcp/sse</code>
					while running. This local-only endpoint allows MCP-compatible clients (Claude Desktop,
					Cursor, VS Code, and others) to query your cards, events, entities, and execute actions
					through Laya.
				</p>
				<p class="mt-2">
					The MCP server is bound to localhost (127.0.0.1) only and is not accessible from the
					network. Access is controlled via bearer token authentication (configurable in Settings
					&rarr; MCP) and tool scope toggles that let you restrict which capabilities are exposed.
					No data is transmitted externally by the MCP server itself — it serves data from your
					local database to clients running on your machine.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">6. Telemetry &amp; Analytics</h2>
				<p>
					Laya does not collect, transmit, or share any usage analytics, telemetry, crash reports,
					or diagnostic data with Laya's contributors or any third party. Dashboard metrics
					(event counts, cost estimates) are computed and stored locally.
				</p>
				<p class="mt-2">
					Laya bundles third-party components (ChromaDB, n8n, sentence-transformers, HuggingFace
					Hub client) that may emit anonymous telemetry to their respective maintainers. Laya
					makes a best-effort attempt to disable all such telemetry via documented opt-out flags
					at startup, but this cannot be guaranteed across all versions of all dependencies. See
					section 9 of the <button class="text-laya-orange underline underline-offset-2 hover:text-laya-gold" onclick={() => (activeTab = 'terms')}>Terms &amp; Conditions</button>
					for details.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">7. Cookies &amp; Tracking</h2>
				<p>
					Laya is a desktop application. It does not use cookies, web beacons, tracking pixels, or
					any browser-based tracking technology. The only browser storage Laya uses is
					<code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">localStorage</code>
					within its own embedded webview for UI preferences (theme, font size, tab state). This
					data never leaves your machine.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">8. Data Retention &amp; Deletion</h2>
				<p>
					All data is stored locally and you have full control over retention. You can configure
					automatic retention periods in <a href="/settings?tab=data" class="text-laya-orange underline underline-offset-2 hover:text-laya-gold">Settings &rarr; Data</a>
					(card retention, chat history retention, audit log retention). You can also export or
					delete all data at any time.
				</p>
				<p class="mt-2">
					To completely remove all Laya data from your system, delete the
					<code class="rounded bg-surface-800 px-1.5 py-0.5 text-xs text-surface-200">~/.laya/</code>
					directory. To revoke platform access, remove the corresponding connections in Settings
					and revoke OAuth grants on each platform's own settings page.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">9. Children's Privacy</h2>
				<p>
					Laya is a professional productivity tool and is not directed at children under 16. By
					using Laya you represent that you are at least the age of majority in your jurisdiction.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">10. Your Rights</h2>
				<p>
					Because Laya is local-first and Laya's contributors do not hold any of your data, there
					is no data controller to whom you need to submit access, rectification, or deletion
					requests. You can inspect, modify, export, or delete all data directly on your machine
					at any time. For data sent to third-party LLM or platform providers, your rights are
					governed by those providers' privacy policies.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">11. Changes to This Policy</h2>
				<p>
					This privacy policy may be updated with new releases of Laya. The "Last updated" date
					at the top reflects the current version. Material changes will be noted in release notes.
				</p>
			</section>

			<section>
				<h2 class="mb-3 text-lg font-semibold text-surface-100">12. Contact</h2>
				<p>
					Laya is an open-source project. For privacy-related questions, open an issue on the
					<a href="https://github.com/aayushch/laya" target="_blank" rel="noopener noreferrer" class="text-laya-orange underline underline-offset-2 hover:text-laya-gold">GitHub repository</a>
					or contact the maintainer directly.
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
