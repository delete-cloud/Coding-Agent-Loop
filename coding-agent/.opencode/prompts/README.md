# OpenCode Prompt Workflow

This directory contains a project-local prompt set for a bounded engineering loop in OpenCode.

The goal is to make the default coding workflow predictable:

- one orchestrator controls scope and stopping conditions
- one engineer implements and runs target tests
- one reviewer reports only `P1/P2` findings
- one verifier reruns the exact target tests

This is intentionally stricter than a free-form agent team. The point is to avoid infinite review loops, speculative refactors, and scope drift.

## Files

Core prompts:

- [`shared-rules.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/shared-rules.md)
- [`task-packet.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/task-packet.md)
- [`orchestrator.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/orchestrator.md)
- [`engineer.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/engineer.md)
- [`reviewer.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/reviewer.md)
- [`verifier.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/verifier.md)

Examples:

- [`examples/checkpoint-slash-command-task-packet.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/examples/checkpoint-slash-command-task-packet.md)
- [`examples/review-fix-loop-task-packet.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/examples/review-fix-loop-task-packet.md)

## Mental Model

Use this prompt set as a bounded loop, not as a general agent persona pack.

The stable assets are:

- shared rules
- role prompts
- a small task packet template

The only thing that should change per task is the task packet:

- goal
- scope
- context
- target tests

## Skill Vs Prompt Set

This repository now has both:

- a repository rule layer in [`AGENTS.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/AGENTS.md)
- a reusable skill in [`.agents/skills/adr-first-workflow/SKILL.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.agents/skills/adr-first-workflow/SKILL.md)
- this OpenCode prompt set in [`.opencode/prompts`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts)

Use them for different purposes.

### Use The Skill First When

- you are not sure whether the task needs an ADR
- you need help deciding what belongs in the task packet
- you need help choosing target tests
- you want the ADR-first workflow and bounded loop explained before execution starts

In other words, use the skill when you still need workflow judgment.

### Use The Prompt Set Directly When

- the task already has a clear goal and scope
- the relevant ADRs are already known
- the target tests are already known
- you are ready to run the engineer/reviewer/verifier loop now

In other words, use the prompt set when the task is already scoped and you want disciplined execution.

### Recommended Combination

For non-trivial work, the best sequence is usually:

1. Use `adr-first-workflow` to decide whether an ADR is needed and to shape the task packet.
2. Fill a task packet.
3. Use this OpenCode prompt set to run the bounded implementation loop.

For small, already-scoped work, you can skip the skill and go straight to the prompt set.

## Recommended Role Split

Default split:

- Orchestrator: strongest reasoning model available
- Engineer: reliable coding model
- Reviewer: strong reasoning model with review discipline
- Verifier: cheaper but reliable model that can rerun commands and report results cleanly

With your current Copilot-accessible models, a practical default is:

- Orchestrator: `github-copilot/claude-opus-4.6` or `github-copilot/gpt-5.4`
- Engineer: `github-copilot/claude-sonnet-4.6` or `github-copilot/gpt-5.4-mini`
- Reviewer: `github-copilot/claude-opus-4.6` or `github-copilot/gpt-5.4`
- Verifier: `github-copilot/claude-sonnet-4.6` or `github-copilot/gpt-5.4-mini`

Rule of thumb:

- use `Opus 4.6` when task boundaries are fuzzy or architecture trade-offs matter
- use `GPT 5.4` when the task is already clear and you want strong orchestration discipline

## Recommended Workflow

### Option A: One Orchestrator, Multiple Worker Sessions

This is the preferred setup when OpenCode lets you create separate tasks or sessions easily.

1. Pick a task packet.
2. Fill in a copy of [`task-packet.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/task-packet.md), or start from one of the examples.
3. Open one orchestrator session with the contents of [`shared-rules.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/shared-rules.md), [`orchestrator.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/orchestrator.md), and the filled task packet.
4. Open one engineer session with [`shared-rules.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/shared-rules.md), [`engineer.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/engineer.md), and the same task packet.
5. After the engineer reports changes and test results, open one reviewer session with [`shared-rules.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/shared-rules.md), [`reviewer.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/reviewer.md), the same task packet, and the engineer's diff summary.
6. If reviewer reports `No P1/P2 findings.`, move directly to verifier.
7. If reviewer reports accepted `P1/P2` findings, send only those findings back to engineer. Do not start a second open-ended review pass.
8. Run verifier with [`shared-rules.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/shared-rules.md), [`verifier.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/verifier.md), and the same task packet.
9. Stop.

### Option B: Manual Single-Thread Orchestration

Use this when separate worker sessions are inconvenient.

In one OpenCode session:

1. Paste `shared-rules + orchestrator + task packet`.
2. Ask the model to explicitly simulate the phases in order:
   - engineer phase
   - reviewer phase
   - engineer fix phase, only if needed
   - verifier phase
3. Keep the stop conditions from the task packet unchanged.

This is less robust than separate worker sessions, but still better than an unbounded free-form loop.

## What To Give Each Role

### Orchestrator Input

- [`shared-rules.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/shared-rules.md)
- [`orchestrator.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/orchestrator.md)
- filled task packet

### Engineer Input

- [`shared-rules.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/shared-rules.md)
- [`engineer.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/engineer.md)
- same filled task packet

### Reviewer Input

- [`shared-rules.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/shared-rules.md)
- [`reviewer.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/reviewer.md)
- same filled task packet
- engineer diff summary and test summary

### Verifier Input

- [`shared-rules.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/shared-rules.md)
- [`verifier.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/verifier.md)
- same filled task packet

## Stop Conditions Matter More Than Prompts

The most important part of this setup is not the wording of the roles. It is the stop policy.

Do not relax these defaults unless you have a concrete reason:

- at most one `review -> fix -> retest` cycle
- only `P1/P2` review findings are in scope
- verifier does not suggest new work
- reviewer does not edit code
- architecture redirection is escalated to the human

If these constraints are removed, the workflow usually drifts into endless polishing.

## How To Use The Example Task Packets

Use [`examples/checkpoint-slash-command-task-packet.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/examples/checkpoint-slash-command-task-packet.md) when you are adding a new feature or command surface.

Use [`examples/review-fix-loop-task-packet.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/examples/review-fix-loop-task-packet.md) when you already have accepted review findings and want a tightly bounded fix loop.

If neither fits exactly, duplicate [`task-packet.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/task-packet.md) and keep it short.

## Relation To `oh-my-openagent`

This prompt set is meant to control the main engineering loop at the project level.

You can keep `oh-my-openagent` installed as an auxiliary toolset, but it should not be the source of truth for how coding tasks are orchestrated in this repository.

Recommended stance:

- keep `oh-my-openagent` for optional helper agents
- use this prompt set for the main implementation/review/verification loop
- remove the plugin later only if this prompt set proves more stable over several real tasks

## Minimal Quick Start

For a new task:

1. Copy [`task-packet.md`](file:///Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent/.opencode/prompts/task-packet.md) or one example packet.
2. Fill `Goal`, `Scope`, `Context`, and `Target tests`.
3. Start the orchestrator with `shared-rules + orchestrator + task packet`.
4. Run engineer.
5. Run reviewer.
6. If needed, run one engineer fix pass.
7. Run verifier.
8. Stop.

If you find yourself wanting a second review loop, that is usually a sign to pause and make a human scope decision.
