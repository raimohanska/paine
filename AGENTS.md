# Agent instructions

- Never read, inspect, or display `.env` files or their contents, including through tools, shell commands, or program output.
- Programs may load `.env` internally at runtime, but agents must not expose the values or credentials.
- After making a fix, run the relevant checks and commit the fix automatically. Include any required app version bump so Home Assistant can offer the update. Do not include unrelated changes, secrets, photos, or diagnostic logs.
- Do not push commits unless the user explicitly requests it.
