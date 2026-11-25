param([ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')][string]$Horario = '07:00')
$ErrorActionPreference = 'Stop'
$python = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe')).Path
$main = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'main.py')).Path
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$name = "FindJobs - Busca diaria ($($userId.Split('\')[-1]))"
$action = New-ScheduledTaskAction -Execute $python -Argument ('"{0}" --diaria' -f $main) -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($Horario, 'HH:mm', $null))
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and ($existing.Actions.Execute -ne $python -or $existing.Actions.WorkingDirectory -ne $PSScriptRoot)) {
    throw "Ja existe uma tarefa com esse nome apontando para outra instalacao."
}
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
$task = Get-ScheduledTask -TaskName $name
$info = Get-ScheduledTaskInfo -TaskName $name
[pscustomobject]@{ Tarefa = $task.TaskName; Estado = $task.State; ProximaBusca = $info.NextRunTime }
