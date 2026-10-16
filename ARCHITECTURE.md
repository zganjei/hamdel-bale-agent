# Runtime architecture

GitHub is the source of truth. A push to main runs unit tests and then the Liara deployment workflow. Only the hosting deployment credential is stored in GitHub Actions secrets; Bale credentials and Codex authentication stay on the persistent server volume/environment.

The process on Liara is a transport and durable job queue. It ingests Bale messages and archives attachments while a separate worker drives the per-chat Codex thread. `codex exec resume THREAD_ID` preserves session state. `config.json` selects the default Luna/low model, timeouts and speech provider; it can be changed in GitHub without editing Liara settings.

Codex autonomously calls a local MCP server for archive search, exact financial calculations, basic arithmetic, and report/XLSX creation. MCP functions operate on read-only snapshots and bounded outputs, without arbitrary command execution. Native shell is disabled because this Liara container cannot create the namespaces required by the Codex shell sandbox. No sandbox or kernel protection is weakened. The full archive is not injected into every prompt. Validated output files are delivered to Bale. Ledger commits remain outside the model workspace and require application-side owner approval. Poster briefs invoke the existing image generation tool; public publishing is not connected.

Persistent data uses the existing archive disk. Existing archive.sqlite3, media, Codex runtime/authentication, and agent-v2 ledger/session paths are retained. Agent-v2 is a data-path name for compatibility, not the deployed software version. No data or authentication files are tracked in git.

Speech is a replaceable service: whisper_cpp runs a quantized model locally; http calls a separately configured private speech service. disabled preserves incoming audio and explains that no reliable transcription provider is configured. Luna/Codex exec does not accept audio input directly. Provider credentials are not exposed to the agent. Transcripts require owner confirmation before entering the agent.

GitHub Actions deploys only after tests pass. A missing LIARA_API_TOKEN secret produces an explicit pending-deployment notice. The release SHA is embedded in release.json and the workflow verifies that exact SHA on /health. One worker replica and the same persistent disk are required. Rollback uses the previous Liara release; disk contents stay intact.
