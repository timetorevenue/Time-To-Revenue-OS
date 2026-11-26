# Claude Workflow Refiner Prompt Template

Use this template after a prepared local run folder has been created with:

```bash
python3 tools/aos-workflow.py prepare <workflow_id>
```

## Prompt

PERMISSION MODE — SCOPED LOCAL TASK APPROVED

Authority: `rules/runtime_permission_policy.md` is the only execution permission policy; this template adds none.

Work only in this prepared run folder:

```text
<prepared_run_folder>
```

Task:

Improve wording, structure, client-readiness, or polish for the prepared local workflow run. Keep the workflow meaning intact.

Required local files:

- `run_packet.md`
- `intake_template.md` or completed intake file
- `output_placeholder.md`
- `receipt_placeholder.md`
- source workflow folder referenced in `run_packet.md`

Rules:

- Local-only edits inside the prepared run folder.
- Use the source workflow folder only as reference unless Casey explicitly expands scope.
- Do not send, publish, upload, mutate CRM records, contact clients, call connectors, or perform external actions.
- Do not create final client-facing claims that are unsupported by the intake or source workflow.
- Flag missing facts, vague promises, pricing uncertainty, confidential data, or risky client commitments.
- Human review is required before any external/client-facing use.
- No external send, publish, CRM update, client delivery, or connector action is allowed without explicit human approval.

Return only:

```text
PASS/NEEDS ATTENTION

Files touched:

* ...

Validation:

* ...

Blockers:

* ...

Next action:

* ...
```
