# Orchestration patterns in the showcase

The interface exposes patterns as observable workflow steps, not as a claim that adding agents always improves outcomes.

| Pattern | Demonstration | Guardrail |
|---|---|---|
| Builder + verifier | Draft builder and a separate verifier invocation receive distinct prompts; live verifier findings are retained and shown beside the review flow. | Verifier cannot edit or approve the draft. Demo mode labels this step simulated and makes no model verifier call. |
| Split work into lanes | Before drafting, three independent investigators analyze technical context, access/privacy, and customer impact. Their bounded findings are shown together and passed to planning as unverified leads. | Lanes cannot take actions or establish facts; evidence IDs are checked against retrieved sources and unknowns remain visible. |
| Best-of-N | In live mode, one to three candidates are generated through separate model calls. The workflow selects the first candidate that passes the available evidence, uncertainty, and sensitive-material boundary checks. | This is first-passing selection, not a ranking across all candidate quality dimensions. If none pass, the first candidate is carried forward and deterministic hard checks can hold the score below approval. The UI shows candidate count and total elapsed time; it does not estimate provider cost. Demo mode uses deterministic response templates. |
| Specialized reviewers | After drafting and verification, separate reviewers score technical correctness, security/privacy, and customer clarity. | Reviewers assess the draft; they do not repeat the investigation tasks, edit the draft, or approve it. Findings are merged by workflow code. |
| Model council | Optional distinct proposal models generate strategies; a separately configured third model scores anonymous proposals for evidence use, uncertainty, safe diagnostics, and next steps. | The judge receives proposal labels A/B without provider labels; configured model names are redacted from the proposal text when they appear verbatim. Use different model families where possible. The judge has no approval or send authority, and its scores are advisory. Configure three distinct model profiles to run the live council. |

## Score gate

The final score is the mean of three reviewer scores, capped below 9 when a hard evidence or sensitive-material check fails. If the first draft review misses the threshold, live mode can make one bounded revision and then runs the verifier and reviewers again; any human edit also requires a fresh verifier/reviewer pass and a separate approval. Demo mode uses small deterministic phrase checks and does not pretend to make model revisions. The UI shows each reviewer score and all retained findings (bounded to limit output size). The verifier's findings remain advisory and visible; it does not silently decide the final score. A 9/10 score is a workflow threshold, not proof of correctness. Human approval is still required before a response is considered ready.

## Evidence and limits

The five patterns in this showcase follow [YK's Google Cloud Community article](https://medium.com/google-cloud/5-agent-orchestration-patterns-backed-by-research-54be9f79171d), originally shared in [this Google Cloud X post](https://x.com/googlecloud/status/2123428849262248255). Google Cloud Community states that authors' views do not necessarily reflect Google's views. The article summarizes several separate studies; its benchmark results are not one universal guarantee about agent teams. In this app, investigation lanes and reviewer lanes are separate stages with different outputs and scopes.

Research reports that collaboration can help or hurt depending on task structure and model capability. It evaluates specific benchmarks and system configurations, so headline results must not be generalized to all support operations. This prototype should be evaluated against a single-agent or deterministic baseline using the same synthetic scenarios, with quality, latency, and cost reported together.

- [Google Research article](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)
- [Peer-reviewed Nature Machine Intelligence article](https://www.nature.com/articles/s42256-026-01268-y)
