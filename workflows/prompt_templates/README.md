# Workflow Prompt Templates

These prompt templates are reusable local wrappers for prepared business workflow run folders. They help Casey route or run one workflow packet without copying the whole workflow library or inventing a new prompt structure each time.

These templates do not execute anything by themselves. They are plain Markdown templates for later human use.

Prepared run folders are created by the workflow shell:

```bash
python3 tools/aos-workflow.py prepare <workflow_id>
```

The shell reads `workflows/workflow_registry.json`, copies or creates an intake file, and writes a local run packet under:

```text
results/workflow_runs/<workflow_id>/<run_id>/
```

Each prepared run should include:

- `run_packet.md`
- `intake_template.md` or completed intake file
- `output_placeholder.md`
- `receipt_placeholder.md`
- a source workflow folder referenced from the run packet

## Template Use

- `codex_workflow_runner.md`: use when Casey wants Codex to do local file-heavy workflow work inside one prepared run folder.
- `claude_workflow_refiner.md`: use when Casey wants Claude Code to improve wording, structure, client-readiness, or polish inside one prepared run folder.
- `human_review_checklist.md`: use before any output becomes external or client-facing.
- `receipt_closeout_template.md`: use to close a completed or blocked workflow run with a consistent receipt.

## Safety Rules

All templates assume local files only. Human review is required before any external use.

No external send, publish, upload, CRM/client delivery, connector action, or client-facing use is allowed without explicit human approval.
