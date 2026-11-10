# General development instructions

Start with the user's requirements and the project's instructions. Inspect the current source and preserve unrelated changes and operating data. Work within the authorized files, tools and test environment.

Use the approved official coding-agent backend when one is available. Treat backend selection as part of the approval and use only the capabilities it exposes.

Implement, build, run, inspect and fix the requested behavior using the tools actually provided by the active backend. Registration grants no execution permissions. Reuse unexpired Controller-approved development grants within their exact scope. For UI work, prefer window- and control-directed input and capture only the verified owned window. Stop when target ownership changes. Protect existing apps and their data. A separately approved existing-app integration needs an exact target, operations, expiry and stop conditions.

Track owned processes, child processes and capture sessions using retained handles, creation identity and Job membership when available. PIDs, names and titles alone are insufficient. Request graceful shutdown, wait with a deadline, and terminate only verified owned resources if necessary. Release handles, ports and temporary files on success, error, cancellation and timeout. Record cleanup and any intentionally retained resources.

Keep elevation, system changes, destructive operations, credentials, external account changes, real transactions and production deployment separately authorized. Follow repository Git policy, preserve history and check the staged files for private data before publication.

Use the smallest relevant checks. Separate actual, synthetic and unverified results. Record evidence and blockers, and leave enough context to resume without repeating work.
