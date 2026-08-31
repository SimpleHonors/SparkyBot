param(
    [switch] $AllowUnsigned,
    [Parameter(Mandatory = $true, Position = 0, ValueFromRemainingArguments = $true)]
    [string[]] $Paths
)

$failed = $false
$unsigned = 0
foreach ($path in $Paths) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-Error "Missing artifact: $path"
        $failed = $true
        continue
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    Write-Host "$path`t$($signature.Status)"
    if ($signature.Status -eq [System.Management.Automation.SignatureStatus]::NotSigned) {
        $unsigned++
    } elseif ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        $failed = $true
    }
}

if ($failed -or ($unsigned -gt 0 -and -not $AllowUnsigned)) {
    exit 1
}
if ($unsigned -gt 0) {
    if ($unsigned -ne $Paths.Count) {
        Write-Error "Mixed signed and unsigned artifact set is not allowed"
        exit 1
    }
    Write-Warning "UNSIGNED INTERNAL CANDIDATE ONLY. DO NOT PUBLISH."
}
exit 0
