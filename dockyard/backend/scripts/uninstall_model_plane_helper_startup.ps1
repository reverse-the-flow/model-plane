param(
  [string]$TaskName = "ModelPlaneLocalHelper"
)

$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Output "Removed Model Plane Local Helper startup task: $TaskName"
} else {
  Write-Output "Model Plane Local Helper startup task was not installed: $TaskName"
}
