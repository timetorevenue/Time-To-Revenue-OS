# Contributing to TTROS Core

Thank you for helping. Core is MIT-licensed; by submitting a contribution you agree it is licensed under the same terms
and that you have the right to submit it. Only submit work you wrote or have permission to relicense. Do not submit
code, prompts or text copied from other projects without stating the source and licence in the pull request.

## The safety model
Contributions are **untrusted**. The maintainers never run contributor code on a machine that holds real credentials,
a real Business Brain or production TTROS data.

1. **Pull requests only, from forks.** CI uses `pull_request` (never `pull_request_target`), a read-only token and no secrets.
2. **Deterministic gate.** `tools/contribution_gate.py` runs from the base branch, not from your branch. It blocks secrets, private-looking
   identifiers (real emails, home paths, private IPs), credential/database/key files, binaries, CI changes and any edit to agent release state.
   Permission-sensitive paths (rules, connectors, authority, packages, dependencies) are flagged for deeper review.
3. **Forge for agents.** A contributed agent package enters only as `candidate`. It earns a state through the Forge evaluation gates
   (schema, cases, held-out cases, independent review) using recorded replies; evaluation makes no model call without maintainer action.
4. **Isolated maintainer review.** Maintainers review risky changes in a disposable container or VM with no credentials, no network to private systems, and a synthetic empty Brain.
5. **Human merge.** Every merge is approved by a maintainer. Automated checks never replace review.

## Before you open a pull request
```bash
python3 tools/contribution_gate.py check --base main --head HEAD
scripts/run_core_tests.sh tests/test_core_clean_install.py tests/test_core_separation.py   # plus tests for your change
```
- Keep changes small and focused; include a test for behaviour you add or fix.
- Follow the layer convention in `context/LAYERS.md`: new files are software, universal knowledge or company data. Company data does not belong here.
- Universal knowledge must be original, company-neutral and registered in `universal/manifest.json`; it is not shareable until a maintainer reviews its rights.
- Use synthetic example data only. Never include real customer, employee or business information.
- Do not add dependencies without a justification and licence in the PR.

Report security problems privately (see `SECURITY.md`), not in issues or pull requests.
