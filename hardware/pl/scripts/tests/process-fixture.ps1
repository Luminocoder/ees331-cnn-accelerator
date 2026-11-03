param([ValidateSet('pass', 'fail', 'wait')][string]$Mode)
if ($Mode -eq 'fail') { Write-Output '[FAIL] deliberate fixture failure'; exit 7 }
if ($Mode -eq 'wait') { Start-Sleep -Seconds 10; exit 0 }
Write-Output 'FIXTURE_PASS'
