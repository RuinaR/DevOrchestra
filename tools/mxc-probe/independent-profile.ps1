# Static contract only. This script neither opens the declared paths nor launches a process.
function New-IndependentMxcProfileDraft {
    param([Parameter(Mandatory)][string]$WorkRoot)

    # Require one local, already-normalized lexical spelling. The owner must
    # separately verify the actual directory and file identities before use.
    if ($WorkRoot -cnotmatch '^[A-Z]:\\.+\\mxc-native-[0-9a-f]{32}\\work\z' -or
        $WorkRoot.Length -gt 240) {
        throw 'NONCANONICAL_WORK_ROOT'
    }
    foreach ($segment in ($WorkRoot.Substring(3) -split '\\')) {
        if ($segment -notmatch '^[\p{L}\p{Nd}_-][\p{L}\p{Nd} ._-]*\z' -or
            $segment.EndsWith('.') -or $segment.EndsWith(' ') -or
            $segment -match '^(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)') {
            throw 'NONCANONICAL_WORK_ROOT'
        }
    }
    $probe = $WorkRoot + '\mxc-probe.exe'
    $key = $probe.Replace('\', '\\')
    $text = @(
        'default_permissions = "mxc-native-minimal"'
        'approval_policy = "never"'
        ''
        '[permissions.mxc-native-minimal.filesystem]'
        ('"{0}" = "read"' -f $key)
        ''
        '[permissions.mxc-native-minimal.network]'
        'enabled = false'
        ''
    ) -join "`n"
    return $text
}

function Test-IndependentMxcProfileContract {
    param(
        [Parameter(Mandatory)][string]$WorkRoot,
        [string]$ProbePath,
        [string]$ProbeSha256,
        [string]$CliSha256,
        [string]$ProfileText,
        [string[]]$WorkspaceRoots,
        # Supplied by trusted host preflight, not by the TOML or the workload.
        [ValidateSet('Unknown', 'Conflict', 'VerifiedAbsent')][string]$ManagementState = 'Unknown'
    )

    $reason = 'RUNTIME_ALLOWLIST_UNVERIFIED'
    $matchesDraft = $false
    try {
        $expected = New-IndependentMxcProfileDraft -WorkRoot $WorkRoot
        if ($ProbePath -cne ($WorkRoot + '\mxc-probe.exe')) {
            $reason = 'PROBE_PATH_MISMATCH'
        } elseif ($ProbeSha256 -cnotmatch '^[0-9a-fA-F]{64}\z' -or
            $ProbeSha256 -ne '5bcaf68f8f75cc58db781812ff84c85e541042b3b7e6504f7d6d09816e598357' -or
            $CliSha256 -ne '4b3286e35d7fe866c6b0eb3f07d7a4cdb5e96489576170dfd6b3a15aa1c67382') {
            $reason = 'BINARY_PIN_MISMATCH'
        } elseif ($WorkspaceRoots.Count -ne 1 -or $WorkspaceRoots[0] -cne $WorkRoot) {
            $reason = 'WORKSPACE_ROOT_MISMATCH'
        } elseif ($ProfileText -cne $expected) {
            # Deliberately reject every noncanonical document, including
            # duplicate or conflicting keys, aliases, and unknown syntax.
            $reason = 'NONCANONICAL_PROFILE'
        } elseif ($ManagementState -ne 'VerifiedAbsent') {
            $reason = 'MANAGED_POLICY_UNVERIFIED'
        } else {
            $matchesDraft = $true
        }
    } catch {
        $reason = 'NONCANONICAL_WORK_ROOT'
    }
    [pscustomobject]@{
        MatchesStaticDraft = $matchesDraft
        Status = if ($matchesDraft) { 'PROFILE_UNSUPPORTED' } else { 'BLOCKED' }
        Reason = $reason
        OsIsolation = 'NOT_VERIFIED'
        ExecutionAllowed = $false
    }
}
