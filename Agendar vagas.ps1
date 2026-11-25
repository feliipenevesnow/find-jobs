param(
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')][string]$HorarioManha = '08:00',
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')][string]$HorarioNoite = '18:00'
)
$ErrorActionPreference = 'Stop'
$python = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe')).Path
$main = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'main.py')).Path
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$shortUser = $userId.Split('\')[-1]

$tasks = @(
    @{
        Name = "FindJobs - Manha Dia Anterior ($shortUser)"
        Time = $HorarioManha
        Args = ('"{0}" --hours 36' -f $main)
    },
    @{
        Name = "FindJobs - Fim do Dia Atual ($shortUser)"
        Time = $HorarioNoite
        Args = ('"{0}" --hours 20' -f $main)
    }
)

$results = foreach ($item in $tasks) {
    $action = New-ScheduledTaskAction -Execute $python -Argument $item.Args -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($item.Time, 'HH:mm', $null))
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $item.Name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    $task = Get-ScheduledTask -TaskName $item.Name
    $info = Get-ScheduledTaskInfo -TaskName $item.Name
    [pscustomobject]@{ Tarefa = $task.TaskName; Estado = $task.State; ProximaBusca = $info.NextRunTime }
}
$results
