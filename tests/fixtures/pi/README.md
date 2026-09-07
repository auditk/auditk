# Pi session fixtures

Real session files written by **pi 0.85.1** (`@earendil-works/pi-coding-agent`,
installed from npm 2026-09-07), generated first-party for the Phase 0 evidence
gate that unblocked the `pi` adapter (see `docs/pi-format-notes.md`).

Provenance, precisely:

- The **writer is real**: every byte of framing (header, entry envelope, ids,
  parent links, roles, pairing, error flags) was produced by pi's own
  `SessionManager` during live `pi --print` runs. The tool executions inside
  are real too — pi genuinely ran `bash`/`read`/`write` in a sandbox workspace
  and recorded the genuine results (including the real ENOENT error in
  `session-error.jsonl`).
- The **model behind pi was scripted**: pi was pointed (via a custom
  `models.json` provider, `api: "openai-completions"`) at a local scripted
  OpenAI-compatible endpoint, so the assistant text/tool-call *content* is
  deterministic by construction. This mirrors the deployment the adapter
  targets (pi against a self-hosted OpenAI-compatible endpoint) and does not
  touch the session-format surface the adapter parses.
- **Anonymisation**: the only post-hoc edit is a mechanical path substitution
  (the generation workspace's absolute path → `/home/user/project`, and the
  parent-session path inside `session-forked.jsonl`'s header likewise). No
  other bytes were altered.

| file | shows |
|---|---|
| `session-rich.jsonl` | v3 header; `model_change` + `thinking_level_change` preamble; user turn; assistant turn with `thinking` + `text` + **two** `toolCall` blocks; two `toolResult` messages with verbatim `toolCallId` echo; second tool round; final text |
| `session-error.jsonl` | a `toolResult` with `isError: true` (real ENOENT from pi's own `read` tool) |
| `session-forked.jsonl` | `parentSession` header field (an absolute *file path*, not an id); parent entries copied **verbatim with their original ids**, then new turns appended |

Regeneration: `scripts/` is not needed — the scripted backend lives with the
session notes; see `docs/pi-format-notes.md` § "How this corpus was generated".
