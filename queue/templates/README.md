# Work Queue Prompt Templates

These are copy/paste workbench templates for turning an Agentic OS Work Queue item into a scoped Codex or Claude prompt.

They do not launch agents and do not change queue state automatically. The operator or future tooling can use them to prepare a workbench prompt from a queue item, then attach the result back to the queue.

Use:

- `codex_task.prompt.md` for Codex-scoped implementation, inspection, edits, and validation,
  in the `rules/prompt_writing.md` one-line-per-part work-order shape. The automated worker path
  builds the same shape in `_queue_codex_execution_instructions`.
- `codex_task_small.prompt.md` for `size: small` Codex work with the operator
  instruction rendered once and no full supervisor/artifact boilerplate.
- `claude_task.prompt.md` for Claude-scoped polish, refactors, precision implementation, and complex assigned work.
- `department_task.prompt.md` for a department-lane work order that runs on the default workbench.
- `revenue_linkedin_outreach.prompt.md` for Revenue-owned LinkedIn relationship outreach prep without external action.
- `factory_build_agent.prompt.md` for a work order that builds one agent through the Agent Factory (`agents/factory/README.md`).
- `receipt.prompt.md` for the durable work receipt shape.

Receipts should be attached back to the queue with:

```bash
python3 tools/aos-queue.py receipt <AOS-ID> <receipt-path> --status <status>
```

Templates reference `rules/runtime_permission_policy.md` once (the launcher supplies it) instead of repeating permission rules.
