# General development instructions

Start with the user's requirements and the project's instructions. Inspect the current source and preserve unrelated changes and operating data. Work within the authorized files, tools and test environment.

For an existing project, recover its approved instructions, handoff, Git baseline, tooling and dependency locks before editing. Keep original source, uncommitted work, models, databases, credentials and local settings protected. Validate real changes in the approved replica and apply only verified changes against a fresh original baseline. Treat live trading, payments, external account changes and deployment as separate approvals. Use simulation with live order submission disabled for trading validation.

Use the approved official coding-agent backend when one is available. Treat backend selection as part of the approval and use only the capabilities it exposes.

Use the owner's frozen role permissions, cycle limit, expiry and token budget for each dispatch. Request a new scoped approval when the goal requires additional permissions. Stop on revocation or an unresolved provider request.

Discover tools from project requirements and lockfiles. Reuse compatible installations and pin the tool identity and dependencies for the approved run. In registered development, use Controller-provisioned portable ZIP tools by their supplied paths when the session has approved them. When an essential dependency is outside that approval, identify its official source, version and integrity evidence for a scope change. Use the official Codex web-search tool for public technical research when the session permits it, and cite the sources used. Keep private code, credentials, local paths and user data out of search queries.

Treat downloading, extracting, and running a tool as distinct capabilities. Use the Controller's approved installer and preserve the installation receipt before reusing the tool.

Implement, build, run, inspect and fix the requested behavior using the tools actually provided by the active backend. Registration grants no execution permissions. Reuse unexpired Controller-approved development grants within their exact scope. For UI work, prefer window- and control-directed input and capture only the verified owned window. Stop when target ownership changes. Protect existing apps and their data. A separately approved existing-app integration needs an exact target, operations, expiry and stop conditions.

Track owned processes, child processes and capture sessions using retained handles, creation identity and Job membership when available. PIDs, names and titles alone are insufficient. Request graceful shutdown, wait with a deadline, and terminate only verified owned resources if necessary. Release handles, ports and temporary files on success, error, cancellation and timeout. Record cleanup and any intentionally retained resources.

Classify work artifacts at each handoff and before completion. Automatically remove only confirmed, unreferenced, disposable workspace files after owned resources have drained. Retain deliverables, evidence and recovery references. Report uncertain old builds, shared tools, caches and tracked documents for review. Protect unrelated data, unresolved work and files in use. Verify cleanup before declaring completion.

Keep elevation, system changes, destructive operations, credentials, external account changes, real transactions and production deployment separately authorized. Follow repository Git policy, preserve history and check the staged files for private data before publication.

Use the smallest relevant checks. Separate actual, synthetic and unverified results. Record evidence and blockers, and leave enough context to resume without repeating work.

## QA time control

Separate AI observation and decision time from the test target's logical time when the distinction matters. Select the smallest supported time model that fits the criterion.

- Use **normal speed** to observe actual user flow, input response, animation timing, frame performance, network latency, real clocks, or long-running behavior.
- Use **slow motion** through the application's or engine's supported time scale to inspect fast transitions. Check which systems use scaled, unscaled, fixed-step, or wall-clock time. Deterministic stepping requires explicit control over logical advances.
- Use **pause and step** when the target supports it. Pause the relevant simulation, capture state, and keep it paused while AI decides. Apply a bounded input batch, advance exactly the approved logical interval or tick count, pause again, and observe and evaluate. Prefer a repeatable initial state and a fixed random seed where relevant.
- Use **virtual time or deterministic events** where screen input is unnecessary. Consider an injectable clock, controlled scheduler, explicit events, reproducible timers, simulated latency, or controlled async completion. Apply this to desktop UI, servers, and other asynchronous applications as appropriate.

Use target-provided controls or approved QA-only code in an approved replica or owned test executable. Leave production behavior intact, keep QA controls out of unapproved deployments, and restore held inputs, pause state, and time settings after success, failure, timeout, or cancellation. Do not change the OS clock or control other processes, external services, or the desktop to alter time. Do not use hooks, injection, or memory changes for time control. If the available tools cannot safely support the intended mode, use an applicable supported check and mark the remaining behavior NOT_VERIFIED.

Keep separate normal-speed checks for time-sensitive claims, including input response, animation, difficulty and balance, FPS and frame time, physics stability, real timers, asynchronous races, long-run stability, and normal user flow where relevant. In existing QA receipts, record the time model, actual elapsed time, logical elapsed time or ticks, scale, input and observation intervals, control source, observed result, and any pending normal-speed verification as relevant. Logical time control never changes the Controller's actual time budget or cancellation deadline.
