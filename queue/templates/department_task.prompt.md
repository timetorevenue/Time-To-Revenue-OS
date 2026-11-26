# Department Queue Task

Authority: `rules/runtime_permission_policy.md` is the only execution permission policy; this prompt adds none.

Use targeted reads/searches. Do not paste or expose secrets.

## Work item

- ID: <AOS-ID>
- Title: <TITLE>
- Owner: <OWNER_OR_AGENT>
- Status: <STATUS>

## Context

<CONTEXT>

## Source references

<SOURCE_REFERENCES>

## Required local references

- `queue/agent_registry.json`
- `queue/templates/receipt.prompt.md`
<CARD_REFERENCE>

## Allowed actions

<ALLOWED_ACTIONS>

## Stop conditions

<STOP_CONDITIONS>

## Definition of done

<DEFINITION_OF_DONE>

## Lane routing

<ROUTING_INSTRUCTION>

Read only the queue item, registry, access model, receipt template, relevant card/context, and source refs needed to complete this item.

## Department card

<CARD_CONTENT>

## Receipt

Return a compact receipt and create or reference a durable receipt after work is complete using `queue/templates/receipt.prompt.md`.

Required closeout format:

PASS/NEEDS ATTENTION

Files touched:
- ...

Validation:
- ...

Blockers:
- ...

Next action:
- ...

Token usage:
- available / unavailable from current CLI output
