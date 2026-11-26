# Codex small repository task

Authority: `rules/runtime_permission_policy.md` is the only execution permission policy; this prompt adds none.

This is one fresh ephemeral Codex session. Do not resume another session.
Work only in `/opt/ttros-core`. Do not send externally, expose
secrets, commit, push, or touch protected client/legacy areas.

Instruction:
<CONTEXT>

Return only the result the instruction requests. Keep reads and validation
proportionate to this small task. Use at most six model/tool turns. Never run a
repo-wide search or broad test suite; if the current state already satisfies
the instruction, run only its focused validation and return.
