param(
    [Parameter(Mandatory = $true, Position = 0, ValueFromRemainingArguments = $true)]
    [string[]] $Paths
)

$failed = $false
foreach ($path in $Paths) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-Error "Missing artifact: $path"
        $failed = $true
        continue
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    Write-Host "$path`t$($signature.Status)"
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        $failed = $true
    }
}

if ($failed) {
    exit 1
}
exit 0
