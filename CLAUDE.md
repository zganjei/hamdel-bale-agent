# Claude Code — Hamdel development
Read docs/HANDOFF.md before changing this repository. Reply to the owner in concise Persian.

This is the Hamdel charity Bale bot, NOT the Sarrafi exchange project. Continuing development with Claude Code does not automatically mean replacing the bot's Codex runtime with Claude. Keep runtime changes separate and verify authentication, deployment and cost before any provider migration.

## Product constraints
- Natural Persian conversation; no required slash commands.
- Ordinary questions use bounded recent group context; historical/periodic reports use the archive and trusted ledger.
- Permanent group archive accepts only the real sender @motahari58. Other senders' forwarded copies do not qualify. Preserve existing sender validation.
- Keep source audio and raw transcription. Owner verifies voice before execution and financial drafts before ledger mutation.
- Preserve records, receipts, session continuity, duplicate protection and the persistent Liara disk.
- Minimize token usage and latency. No automatic model escalation, paid API activation or hosting upgrade.
- Do not mix in Sarrafi rules (such as treating every receipt as confirmed).

## Development
Python 3.12. Install requirements.txt in a virtual environment.
Run: python -m unittest discover -s tests -v
Then: python -m compileall -q hamdel main.py agent_bridge.py multimodal.py media_reader.py
Use temporary DATA_DIR and mocked external services for tests. Do not launch a second production poller.
Production entrypoint is python main.py; do not also run python -m hamdel.

## Operations and evidence
Read .github/workflows/ci.yml and scripts/deploy.py before deployment. Main pushes can deploy.
One polling replica, Liara hamdel-reporter, archive disk at /usr/src/app/data.
Never log or commit credentials, auth files, session dumps, private audio or databases. Use existing secret stores. Do not copy developer credentials into runtime configuration.
Do not disable sandboxing to work around host limitations; bounded MCP tools are already used.
Check live /health and deployed commit after a runtime release. Passing mocked tests is not proof of Persian transcription quality or group-message delivery.
Update docs/HANDOFF.md with changes, tests, deployed SHA and remaining blockers, clearly distinguishing observed facts from historical reports.
