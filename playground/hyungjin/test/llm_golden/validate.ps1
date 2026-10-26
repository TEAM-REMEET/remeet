$ErrorActionPreference = 'Stop'

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$testRoot = Split-Path -Parent $PSScriptRoot
$totalBoundaries = 0
$totalCandidates = 0
foreach ($meetingId in @('TS3003a', 'TS3003b', 'TS3003c', 'TS3003d')) {
    $gold = Get-Content -LiteralPath (Join-Path $PSScriptRoot "$meetingId.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $meeting = Get-Content -LiteralPath (Join-Path $testRoot "meetings/$meetingId.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $lines = @(Get-Content -LiteralPath (Join-Path $testRoot "input/$meetingId.txt") -Encoding UTF8 | Where-Object { $_.Trim().Length -gt 0 })
    $utterances = @($lines | Select-Object -Skip 3)
    Assert-True ($gold.meeting_id -eq $meetingId) "$meetingId : 회의 ID 불일치"
    Assert-True ($utterances.Count -eq $gold.utterance_count -and $utterances.Count -eq $meeting.utterances.Count) "$meetingId : 발화 수 불일치"
    for ($i = 0; $i -lt $utterances.Count; $i++) {
        Assert-True ($utterances[$i] -ceq $meeting.utterances[$i].text_ko) "$meetingId : 입력과 한국어 발화 불일치 [$($i + 1)]"
        Assert-True ($meeting.utterances[$i].uid -eq ('u{0:D4}' -f ($i + 1))) "$meetingId : 발화 ID 불일치"
    }
    $previous = 1
    $accepted = @()
    foreach ($c in $gold.candidates) {
        Assert-True ($c.position -gt $previous -and $c.position -le $utterances.Count) "$meetingId : 후보 순서 또는 범위 오류"
        $previous = $c.position
        Assert-True ($c.before_subject -and $c.after_subject -and $c.evidence) "$meetingId : 필수 설명 누락"
        Assert-True ($c.shift_type -in @('new_agenda', 'same_subject_aspect', 'digression_return', 're_discussion', 'no_shift')) "$meetingId : 잘못된 판정 유형"
        foreach ($name in @('subject_change', 'closure_signal', 'independent_summary')) {
            Assert-True ($c.checks.$name -is [bool]) "$meetingId : 질문 판정이 불리언이 아님"
        }
        $anyCheck = $c.checks.subject_change -or $c.checks.closure_signal -or $c.checks.independent_summary
        $isBoundary = $c.shift_type -eq 'new_agenda'
        Assert-True ($c.shift_result -is [bool] -and $c.shift_result -eq $isBoundary) "$meetingId : shift_result 불일치"
        Assert-True ($c.verdict -ceq $(if ($isBoundary) { 'O' } else { 'X' })) "$meetingId : verdict 불일치"
        if ($isBoundary) {
            Assert-True $anyCheck "$meetingId : 참인 질문 없는 새 경계"
            $accepted += $c.position
        }
        if ($c.shift_type -eq 'no_shift') { Assert-True (-not $anyCheck) "$meetingId : no_shift 규칙 위반" }
        $citations = [regex]::Matches($c.evidence, '\[(\d+)\]')
        Assert-True ($citations.Count -ge 2) "$meetingId : 근거 인용 부족"
        foreach ($citation in $citations) {
            $number = [int]$citation.Groups[1].Value
            Assert-True ($number -ge 1 -and $number -le $utterances.Count) "$meetingId : 인용 범위 오류"
        }
        Assert-True ($c.verification.checked -eq $true) "$meetingId : 재검토 확인 누락"
    }
    Assert-True (($accepted -join ',') -eq ($gold.boundary_positions -join ',')) "$meetingId : 경계 목록 불일치"
    Assert-True ($gold.segments.Count -eq $accepted.Count + 1) "$meetingId : 구간 수 불일치"
    $nextStart = 1
    foreach ($s in $gold.segments) {
        Assert-True ($s.start_utterance -eq $nextStart -and $s.end_utterance -ge $s.start_utterance) "$meetingId : 구간 누락 또는 중복"
        Assert-True ($s.subject -and $s.summary) "$meetingId : 구간 설명 누락"
        $expectedExcluded = @($gold.candidates | Where-Object { -not $_.shift_result -and $_.position -ge $s.start_utterance -and $_.position -le $s.end_utterance } | ForEach-Object { $_.position })
        Assert-True (($expectedExcluded -join ',') -eq ($s.excluded_candidate_positions -join ',')) "$meetingId : 제외 후보 연결 불일치"
        $nextStart = $s.end_utterance + 1
    }
    Assert-True ($nextStart -eq $utterances.Count + 1) "$meetingId : 마지막 구간 범위 오류"
    $segmentStarts = @($gold.segments | Select-Object -Skip 1 | ForEach-Object { $_.start_utterance })
    Assert-True (($segmentStarts -join ',') -eq ($accepted -join ',')) "$meetingId : 구간 시작과 경계 불일치"
    $totalBoundaries += $accepted.Count
    $totalCandidates += $gold.candidates.Count
    Write-Output "$meetingId : 통과 / 발화 $($utterances.Count), 경계 $($accepted.Count), 구간 $($gold.segments.Count), 후보 $($gold.candidates.Count)"
}
Write-Output "전체 통과 / 경계 $totalBoundaries, 후보 $totalCandidates"
