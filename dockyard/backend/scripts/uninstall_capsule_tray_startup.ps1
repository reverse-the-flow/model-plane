param(
  [string]$TaskName = "ModelPlaneCapsuleHandoffTray"
)

$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Output "Removed Capsule Handoff Tray startup task: $TaskName"
} else {
  Write-Output "Capsule Handoff Tray startup task was not installed: $TaskName"
}
