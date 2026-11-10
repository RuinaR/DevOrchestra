# General development instructions

Start with the user's requirements and the project's instructions. Inspect the current source and preserve unrelated changes and operating data. Work within the authorized files, tools and test environment.

Implement, build, run, inspect and fix the requested behavior. For UI work, exercise the approved test application, inspect its rendering and capture only its owned window when useful. Use supported, scoped tools. Ask only when scope or a consequential decision is unclear.

Track owned processes, child processes and capture sessions. Request graceful shutdown, wait with a deadline, and terminate only verified owned resources if necessary. Release handles, ports and temporary files on success, error, cancellation and timeout. Record cleanup and any intentionally retained resources.

Keep elevation, system changes, destructive operations, credentials, external account changes, real transactions and production deployment separately authorized. Follow repository Git policy, preserve history and check the staged files for private data before publication.

Use the smallest relevant checks. Separate actual, synthetic and unverified results. Record evidence and blockers, and leave enough context to resume without repeating work.
