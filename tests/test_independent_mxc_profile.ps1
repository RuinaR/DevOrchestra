$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\tools\mxc-probe\independent-profile.ps1')

$root = 'D:\합성 Lab\mxc-native-0123456789abcdef0123456789abcdef\work'
$draft = New-IndependentMxcProfileDraft -WorkRoot $root
$baseline = @{
    WorkRoot = $root
    ProbePath = $root + '\mxc-probe.exe'
    ProbeSha256 = '5bcaf68f8f75cc58db781812ff84c85e541042b3b7e6504f7d6d09816e598357'
    CliSha256 = '4b3286e35d7fe866c6b0eb3f07d7a4cdb5e96489576170dfd6b3a15aa1c67382'
    ProfileText = $draft
    WorkspaceRoots = @($root)
    ManagementState = 'VerifiedAbsent'
}

function Assert-Outcome($case, $reason, $status = 'BLOCKED') {
    $actual = Test-IndependentMxcProfileContract @case
    if ($actual.Reason -cne $reason -or $actual.Status -cne $status -or $actual.ExecutionAllowed -or
        $actual.OsIsolation -cne 'NOT_VERIFIED' -or $actual.MatchesStaticDraft -ne ($status -eq 'PROFILE_UNSUPPORTED')) {
        throw "Unexpected static outcome for $reason"
    }
}

function New-Case($change) {
    $case = $baseline.Clone()
    foreach ($key in $change.Keys) { $case[$key] = $change[$key] }
    return $case
}

if (!$draft.Contains('"D:\\합성 Lab\\mxc-native-0123456789abcdef0123456789abcdef\\work\\mxc-probe.exe" = "read"')) {
    throw 'Absolute path TOML escaping mismatch'
}
Assert-Outcome $baseline 'RUNTIME_ALLOWLIST_UNVERIFIED' 'PROFILE_UNSUPPORTED'
foreach ($change in @(
    @{ ProfileText = $draft + 'extends = ":workspace"' },
    @{ ProfileText = $draft.Replace('enabled = false', 'enabled = true') },
    @{ ProfileText = $draft.Replace('"never"', '"on-request"') },
    @{ ProfileText = $draft.Replace('"read"', '"write"') },
    @{ ProfileText = $draft.Replace(' = "read"', " = `"read`"`n`":root`" = `"read`"") },
    @{ ProfileText = $draft.Replace(' = "read"', " = `"read`"`n`":minimal`" = `"read`"") },
    @{ ProfileText = $draft.Replace(' = "read"', ' = "deny"') },
    @{ ProfileText = $draft + $draft },
    @{ ProfileText = $draft + "`n[permissions.mxc-native-minimal.workspace_roots]" },
    @{ ProfileText = $draft + "`n[unsupported]" }
)) {
    Assert-Outcome (New-Case $change) 'NONCANONICAL_PROFILE'
}
Assert-Outcome (New-Case @{WorkspaceRoots = @($root, 'D:\extra')}) 'WORKSPACE_ROOT_MISMATCH'
Assert-Outcome (New-Case @{WorkspaceRoots = @()}) 'WORKSPACE_ROOT_MISMATCH'
Assert-Outcome (New-Case @{ProbePath = 'D:\other\mxc-probe.exe'}) 'PROBE_PATH_MISMATCH'
Assert-Outcome (New-Case @{ProbeSha256 = '0' * 64}) 'BINARY_PIN_MISMATCH'
Assert-Outcome (New-Case @{CliSha256 = '0' * 64}) 'BINARY_PIN_MISMATCH'
Assert-Outcome (New-Case @{ManagementState = 'Conflict'}) 'MANAGED_POLICY_UNVERIFIED'
Assert-Outcome (New-Case @{ManagementState = 'Unknown'}) 'MANAGED_POLICY_UNVERIFIED'
foreach ($unsafe in @(
    $root.Replace('합성 Lab', '..'), $root.Replace('합성 Lab', 'NUL'),
    $root.Replace('합성 Lab', 'alias.'), $root.Replace('합성 Lab', '[wildcard]'),
    $root.Replace('D:', '\\server\share'), $root.Replace('\work', '\work\extra')
)) {
    Assert-Outcome (New-Case @{WorkRoot = $unsafe}) 'NONCANONICAL_WORK_ROOT'
}
'Independent MXC profile static cases passed'
