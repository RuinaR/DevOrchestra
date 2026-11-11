# General development instructions

Start with the user's requirements and the project's instructions. Inspect the current source and preserve unrelated changes and operating data. Work within the authorized files, tools and test environment.

For an existing project, recover its approved instructions, handoff, Git baseline, tooling and dependency locks before editing. Keep original source, uncommitted work, models, databases, credentials and local settings protected. Validate real changes in the approved replica and apply only verified changes against a fresh original baseline. Treat live trading, payments, external account changes and deployment as separate approvals. Use simulation with live order submission disabled for trading validation.

Use the approved official coding-agent backend when one is available. Treat backend selection as part of the approval and use only the capabilities it exposes.

Use the owner's frozen role permissions, cycle limit, expiry and token budget for each dispatch. Request a new scoped approval when the goal requires additional permissions. Stop on revocation or an unresolved provider request.

Discover tools from project requirements and lockfiles. Reuse compatible installations and pin the tool identity and dependencies for the approved run. When a dependency is missing, request an approved project-local installation with verified provenance and an explicit network and execution scope.

Treat downloading, extracting, and running a tool as distinct capabilities. Use the Controller's approved installer and preserve the installation receipt before reusing the tool.

Implement, build, run, inspect and fix the requested behavior using the tools actually provided by the active backend. Registration grants no execution permissions. Reuse unexpired Controller-approved development grants within their exact scope. For UI work, prefer window- and control-directed input and capture only the verified owned window. Stop when target ownership changes. Protect existing apps and their data. A separately approved existing-app integration needs an exact target, operations, expiry and stop conditions.

Track owned processes, child processes and capture sessions using retained handles, creation identity and Job membership when available. PIDs, names and titles alone are insufficient. Request graceful shutdown, wait with a deadline, and terminate only verified owned resources if necessary. Release handles, ports and temporary files on success, error, cancellation and timeout. Record cleanup and any intentionally retained resources.

Classify work artifacts at each handoff and before completion. Automatically remove only confirmed, unreferenced, disposable workspace files after owned resources have drained. Retain deliverables, evidence and recovery references. Report uncertain old builds, shared tools, caches and tracked documents for review. Protect unrelated data, unresolved work and files in use. Verify cleanup before declaring completion.

Keep elevation, system changes, destructive operations, credentials, external account changes, real transactions and production deployment separately authorized. Follow repository Git policy, preserve history and check the staged files for private data before publication.

Use the smallest relevant checks. Separate actual, synthetic and unverified results. Record evidence and blockers, and leave enough context to resume without repeating work.
