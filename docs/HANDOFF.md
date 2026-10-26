# Hamdel handoff to Claude Code
Prepared 2026-10-26. Source reviewed: 7f3ad7e6b97e0aa723bf3b7528e6d30680c07729.

## Scope and current evidence
The owner wants to continue development in Claude Code. This handoff adds development guidance, not a runtime-provider migration.
Repository: https://github.com/zganjei/hamdel-bale-agent (private).
Bale bot: @hamdel_report_bot. Liara app: hamdel-reporter.
Latest source reviewed includes RAM group context and startup seeding from the approved archive sender.
A prior conversation reported this commit deployed on 2026-10-16 and 64 passing tests. These are historical reports, not new production verification on 2026-10-26.
Older findings about f90cdc0c being live must not be treated as current.
This handoff does not contain secrets, production data or Codex authentication. A fresh clone alone is not a runnable production replica.

## Product intent
A simple general-purpose Persian charity assistant: conversation, categorization of received group messages, charity appeals, reports, CSV/XLSX and poster requests. Low cost and low latency matter.
Ordinary conversation should use recent group context, without searching the historical archive for every question. Reports and exact historical totals need archive/ledger evidence.
Only messages genuinely sent by @motahari58 enter the permanent group archive. Do not delete old records to enforce this; keep ineligible records excluded from normal access.
Group visibility is bounded and may be incomplete. Do not claim to have read messages that Bale never delivered.
Ask for the SEK-to-toman selling rate for each conversion report; never confuse toman and rial.
This project is unrelated to the Sarrafi accounting bot.

## Code map
- main.py: production polling, queues, archive filtering, media/speech workers, live context, /health.
- hamdel/live_context.py: heuristic report routing and category hints; 120 live messages / 48 hours; snapshots up to 28 messages / 8500 characters. Startup seed: up to 60 approved sender messages within 60 days.
- agent_bridge.py: connects queues/context to agent; voice and draft confirmation; artifact delivery.
- hamdel/codex_agent.py: resumable per-chat Codex sessions and bounded MCP integration.
- hamdel/mcp_tools.py: archive, reporting, calculation and file tools.
- hamdel/store.py: trusted records and confirmation state, outside agent workspace.
- hamdel/speech.py: Cloudflare transcription, optional two-pass ASR, text proofreading and raw preservation.
- multimodal.py: separate ephemeral Codex invocations for media/proofreading/posters.
- workspace_template/AGENTS.md: instructions for the RUNNING bot agent; not the development handoff.
- config.json: Luna/low for the main agent, Cloudflare speech, dual pass for short audio, approved sender.
- .github/workflows/ci.yml: tests, deploy, exact release check and synthetic speech check.
- scripts/deploy.py, scripts/prepare_release.py, scripts/sync_liara_speech_env.py: existing release path.

## First tasks, in priority order
1. Diagnose the owner's actual failure: bot says no recent group context. Check /health group_updates_since_boot, group_last_received_at, group_seeded_messages, bound chat and Bale delivery permissions. Trace handle -> LIVE_GROUP -> answer_ai -> agent_bridge -> prompt. Distinguish no deliveries from prompt construction or wrong chat. A seeded archive is not proof of live receipt.
2. Fix full voice review: agent_bridge.voice_ready currently shows proposed[:3300] but approves the entire stored transcript. Show all chunks before offering confirmation; preserve idempotent callbacks and handle partial send failure. Raw display is also truncated to 3500.
3. Make proofreading model/effort explicit: multimodal.codex currently omits both despite config.json specifying Luna/low for the main agent. Apply suitable explicit config to text proofreading; do not accidentally break the image-generation path. Test actual subprocess arguments without model/network calls.
4. Measure latency/token usage before optimization. Dual-pass speech is enabled for audio <=90s and costs additional Cloudflare quota; benchmark actual Persian clips, names and amounts against an owner-verified transcript. Text proofreading cannot recover missing audio evidence. No fabricated words or blanket quality guarantees.
5. Audit privacy claims: README says other members' live context is RAM-only, but prompts are sent to a resumable Codex session, which may persist their contents. Trace session and logging retention; do not promise end-to-end RAM-only retention without proof. Explain and resolve the distinction between charity archive storage and model session history.
6. Routing/category labels are heuristics, not semantic guarantees. Improve only demonstrated failures, keeping ordinary conversations lightweight.
7. Review startup seed ageing: seed() checks 60-day age on load; snapshot() does not re-expire baseline records during a long uptime.

These are source-review findings, not changes implemented by this handoff.

## Safe setup and validation
Clone using the developer's existing GitHub authentication:
    git clone https://github.com/zganjei/hamdel-bale-agent.git
    cd hamdel-bale-agent
    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install -r requirements.txt
    python -m unittest discover -s tests -v
    python -m compileall -q hamdel main.py agent_bridge.py multimodal.py media_reader.py
Windows: activate the equivalent .venv Scripts environment.
Tests must use temporary storage and mocked Bale/ASR/model calls. Do not run main.py with the production bot token locally while Liara is polling.
Add regression coverage for each behavioral change. Report actual outcomes, not the historical test count.

## Deployment, data and access
Production entrypoint: python main.py. Only one poller.
Python 3.12; Liara iran region, port 8080. Disk archive mounted at /usr/src/app/data.
Persistent paths include data/archive.sqlite3, data/media, data/agent-v2/trusted, data/agent-v2/chats and data/hamdel-codex-auth.
Codex runtime currently expected at data/codex-runtime/package/vendor/x86_64-unknown-linux-musl/bin/codex. ffmpeg/ffprobe are required for speech preprocessing.
Secret NAMES only:
- Liara environment: BALE_BOT_TOKEN; speech account/token if configured.
- GitHub Actions: LIARA_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN.
- Codex login state: persistent data/hamdel-codex-auth; authenticate via supported flow if needed, never paste auth files in chat/Git.
GitHub secret values cannot be recovered by reading the repo. Ask only for a genuinely missing access step, through a secure channel; do not request broad credential dumps.
Before migrations, take a consistent SQLite backup and preserve media/receipts, trusted ledger, auth and session state. A filesystem copy of an actively written SQLite WAL database is not necessarily a consistent backup.
Read the current workflow before push: main pushes deploy. For this documentation-only handoff, commit uses [skip ci] to avoid an unnecessary production restart.
After runtime deployment verify https://hamdel-reporter.liara.run/health: exact expected SHA, fresh polling and speech state. Verify group delivery separately. A health response alone does not prove a successful conversation.
Rollback code through the normal release process to a known-good revision; do not reset financial data. Record backward-compatibility for any schema migration.
Historical Liara shell sandbox failed on unprivileged namespaces; existing MCP tools avoid needing unrestricted shell. Preserve this approach.

## Completion criteria
Natural-language group categorization uses actually received context; historical report accesses the correct filtered evidence; no hidden voice tail is approved; retries do not duplicate financial entries; configured lightweight model is explicit for proofreading; source and deployed release are traceable. Describe untested integration paths honestly.
