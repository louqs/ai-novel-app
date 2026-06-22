param([string]$FilePath)

$lines = Get-Content $FilePath -Encoding UTF8
# Extract body only (before ## 作者有话说)
$bodyLines = @()
$inBody = $false
foreach ($line in $lines) {
    if ($line -match '^## 作者有话说') { break }
    if ($line.Trim().Length -gt 0 -and $line -notmatch '^# ') { $inBody = $true }
    if ($inBody) { $bodyLines += $line }
}

# Find repeated 4+ line blocks
$minBlock = 4
$found = @()
for ($i = 0; $i -lt $bodyLines.Count - $minBlock; $i++) {
    for ($blk = $minBlock; $blk -le 8; $blk++) {
        if ($i + $blk -ge $bodyLines.Count) { break }
        $block = $bodyLines[$i..($i+$blk-1)] -join "`n"
        if (($block.Trim() -replace '\s','').Length -lt 30) { continue }
        for ($j = $i + $blk; $j -lt $bodyLines.Count - $blk; $j++) {
            $block2 = $bodyLines[$j..($j+$blk-1)] -join "`n"
            if ($block -eq $block2) {
                $first = ($block -replace '\n',' ').Substring(0, [Math]::Min(80, $block.Length))
                Write-Host "DUPLICATE: L$($i+1) == L$($j+1) ($blk lines): $first..."
                $found += "[L$($i+1)-L$($i+$blk)] == [L$($j+1)-L$($j+$blk)]"
                $i = $j + $blk - 1
                break
            }
        }
    }
}
if ($found.Count -eq 0) { Write-Host "OK - no duplicates" } else { Write-Host "TOTAL: $($found.Count) duplicate block(s)" }
