---
name: review
description: Reviews code changes and provides constructive feedback. Should be used when a review is requested to provide a consistent review behavior and output format. This skill can be used for code reviews in general, not just for GitHub pull requests.
version: 1.0.0
---

# Review Code Changes

Provide a consistent, constructive code review with a fixed output format. This skill works for any
code review — uncommitted working-tree changes, a branch diff, or a GitHub pull request.

## Scope (do this — nothing more)

- **Just review. DO NOT make any changes** to any file.
- **Do NOT run tests or linters.** This is a read-and-reason review only.
- **Do NOT highlight things that are already good** — only call out what needs attention.
- Be **constructive and specific**; suggest improvements where appropriate.

## Step 1 — Determine what to review

Identify the change set before reviewing:

- If the user named a PR, branch, or commit range, review that (`git diff <base>...<head>`, or fetch
  the PR diff).
- Otherwise default to the local changes: `git status` then `git diff` (and `git diff --staged`).
- If there are no changes, say so and stop.

Read the full diff plus enough surrounding context in each touched file to judge correctness — don't
review hunks in isolation.

## Step 2 — Analyze the changes

Review the code changes for:

- **Code quality and style consistency** — does it match the surrounding code's conventions?
- **Potential bugs or issues** — logic errors, edge cases, null/None handling, race conditions.
- **Performance implications** — inefficient algorithms, needless work, N+1 patterns.
- **Security concerns** — injection, secrets, unsafe deserialization, auth/validation gaps.
- **Test coverage** — are new behaviors/branches covered? Are there gaps?
- **Documentation updates if needed** — docstrings, README, comments that are now stale.

Record each finding as a candidate with: file, line number (when determinable), severity
(`CRITICAL` / `PROBLEM` / `SUGGESTION`), and a one-line description.

## Step 3 — Verify each finding with parallel subagents

After drafting the findings, **double-check each one** before reporting it:

- Spawn **one subagent per finding** using the Agent tool (subagent_type `general-purpose`).
- Run them in parallel, but **no more than 10 at a time** — batch if there are more than 10 findings.
- Each subagent's job: independently confirm whether the finding is real. Give it the file path,
  the relevant line(s)/snippet, and the claim. Instruct it to read the actual code and respond with
  a verdict (**confirmed** / **refuted** / **uncertain**), a short justification, and a corrected
  line number if the original was off. The subagent must **not** modify any files.
- Gather the verdicts. **Drop or downgrade refuted findings.** Keep confirmed ones; for uncertain
  ones, keep them but mark the uncertainty. Fold the corrected details into the final comments.

## Step 4 — Output

Use exactly this format.

First, list specific comments per file/line that needs attention (group by file). Include the file
and line number whenever possible.

Then end with an overall assessment line and a bullet-point list of suggested changes. The
assessment is one of: **approve**, **request changes**, or **comment**.

Severity tags for bullets: `[CRITICAL]`, `[PROBLEM]`, `[SUGGESTION]`.

### Example output

```
Overall assessment: request changes.
- [CRITICAL] sensor.py:143 - Memory leak
- [PROBLEM] data_processing.py:87 - Inefficient algorithm
- [SUGGESTION] test_init.py:45 - Improve x variable name
```

Always include the file and line number in the bullet points when possible. If there are no
findings, give an `approve` assessment and say there are no suggested changes.
