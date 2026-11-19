# Agent instructions

- Before working on water-meter behavior, read `docs/water-logic.md`. The specification and the behavior it freezes are binding. Get explicit user approval for the specific change before altering either, including through defaults, thresholds, models, or tests. A request to investigate is not approval to change behavior. If the spec, code, and tests disagree, report the conflict and ask before resolving it.

- When referring to local files or images, show the repository-relative path as inline code (for example, `snapshots/run/preview.png`) so the user can copy it. Do not use Markdown links or image embeds for local files.

- Never read, inspect, or display `.env` files or their contents, including through tools, shell commands, or program output.
- Programs may load `.env` internally at runtime, but agents must not expose the values or credentials.
- After making a fix, run the relevant checks and commit the fix automatically. Include any required app version bump so Home Assistant can offer the update. Do not include unrelated changes, secrets, photos, or diagnostic logs.
- Do not push commits unless the user explicitly requests it.
