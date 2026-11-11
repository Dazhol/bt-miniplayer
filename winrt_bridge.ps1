[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime

# C# CoreAudio helper for controlling any Bluetooth A2DP Sink volume, mute, peak meter, and detecting Teams calls
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[Guid("BCDE0395-E52F-467C-8E3D-C4579291692E"), ComImport]
class MMDeviceEnumeratorComObject { }

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int dataFlow, int dwStateMask, out IMMDeviceCollection ppDevices);
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppEndpoint);
}

[Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceCollection {
    int GetCount(out int pcDevices);
    int Item(int nDevice, out IMMDevice ppDevice);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
    int OpenPropertyStore(int stgmAccess, out IntPtr ppProperties);
    int GetId([MarshalAs(UnmanagedType.LPWStr)] out string ppstrId);
    int GetState(out int pdwState);
}

[Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionManager2 {
    int GetAudioSessionControl(IntPtr AudioSessionGuid, int StreamFlags, out IntPtr SessionControl);
    int GetSimpleAudioVolume(IntPtr AudioSessionGuid, int StreamFlags, out IntPtr AudioVolume);
    int GetSessionEnumerator(out IAudioSessionEnumerator SessionEnum);
}

[Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionEnumerator {
    int GetCount(out int SessionCount);
    int GetSession(int SessionCount, out IAudioSessionControl Session);
}

[Guid("F4B1A599-7266-4319-A8CA-E70ACB11E8CD"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionControl {
    int GetState(out int pRetVal);
    int GetDisplayName([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
    int SetDisplayName([MarshalAs(UnmanagedType.LPWStr)] string Value, ref Guid EventContext);
    int GetIconPath([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
    int SetIconPath([MarshalAs(UnmanagedType.LPWStr)] string Value, ref Guid EventContext);
    int GetGroupingParam(out Guid pRetVal);
    int SetGroupingParam(ref Guid Override, ref Guid EventContext);
    int RegisterAudioSessionNotification(IntPtr NewNotifications);
    int UnregisterAudioSessionNotification(IntPtr NewNotifications);
}

[Guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionControl2 {
    int GetState(out int pRetVal);
    int GetDisplayName([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
    int SetDisplayName([MarshalAs(UnmanagedType.LPWStr)] string Value, ref Guid EventContext);
    int GetIconPath([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
    int SetIconPath([MarshalAs(UnmanagedType.LPWStr)] string Value, ref Guid EventContext);
    int GetGroupingParam(out Guid pRetVal);
    int SetGroupingParam(ref Guid Override, ref Guid EventContext);
    int RegisterAudioSessionNotification(IntPtr NewNotifications);
    int UnregisterAudioSessionNotification(IntPtr NewNotifications);
    int GetSessionIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
    int GetSessionInstanceIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string pRetVal);
    int GetProcessId(out uint pRetVal);
    int IsSystemSoundsSession();
    int SetDuckingPreference(bool optOut);
}

[Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ISimpleAudioVolume {
    int SetMasterVolume(float fLevel, ref Guid EventContext);
    int GetMasterVolume(out float pfLevel);
    int SetMute(bool bMute, ref Guid EventContext);
    int GetMute(out bool pbMute);
}

[Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioMeterInformation {
    int GetPeakValue(out float pfPeak);
}

public class BtMixerStatus {
    public bool FoundSession;
    public int SessionState;
    public string SessionName;
    public float Volume;
    public bool Muted;
    public float Peak;
    public bool TeamsActive;
}

public static class BtAudioMixer {
    private static bool IsBtSession(string disp, string sid) {
        string d = (disp ?? "").ToLowerInvariant();
        string s = (sid ?? "").ToLowerInvariant();
        return d.Contains("a2dp") || d.Contains("snk") || d.Contains("bluetooth") || s.Contains("a2dp") || s.Contains("bth");
    }

    public static BtMixerStatus GetStatus(string deviceHint) {
        var res = new BtMixerStatus { FoundSession = false, SessionState = 0, SessionName = "", Volume = 1.0f, Muted = false, Peak = 0.0f, TeamsActive = false };
        try {
            var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
            IMMDevice dev;
            enumerator.GetDefaultAudioEndpoint(0, 1, out dev);
            Guid iidMgr = typeof(IAudioSessionManager2).GUID;
            object objMgr;
            dev.Activate(ref iidMgr, 23, IntPtr.Zero, out objMgr);
            var mgr = (IAudioSessionManager2)objMgr;
            IAudioSessionEnumerator sesEnum;
            mgr.GetSessionEnumerator(out sesEnum);
            int count;
            sesEnum.GetCount(out count);
            string hint = (deviceHint ?? "").ToLowerInvariant();
            int bestScore = -1;

            for (int i = 0; i < count; i++) {
                IAudioSessionControl ctrl;
                sesEnum.GetSession(i, out ctrl);
                var ctrl2 = (IAudioSessionControl2)ctrl;
                var vol = (ISimpleAudioVolume)ctrl;
                var meter = (IAudioMeterInformation)ctrl;
                string disp, sid;
                int st;
                ctrl2.GetDisplayName(out disp);
                ctrl2.GetSessionIdentifier(out sid);
                ctrl2.GetState(out st);
                float peak;
                meter.GetPeakValue(out peak);

                string sidLower = (sid ?? "").ToLowerInvariant();
                if (sidLower.Contains("teams") && peak > 0.01f) {
                    res.TeamsActive = true;
                }

                bool matchHint = !string.IsNullOrEmpty(hint) && ((disp ?? "").ToLowerInvariant().Contains(hint));
                bool isBt = IsBtSession(disp, sid);
                if (isBt || matchHint) {
                    int score = (matchHint ? 4 : 0) + (st == 1 ? 2 : 0) + (peak > 0.001f ? 1 : 0);
                    if (score >= bestScore) {
                        bestScore = score;
                        float v;
                        bool m;
                        vol.GetMasterVolume(out v);
                        vol.GetMute(out m);
                        res.FoundSession = true;
                        res.SessionState = st;
                        res.SessionName = disp ?? "Audio Bluetooth";
                        res.Volume = v;
                        res.Muted = m;
                        res.Peak = peak;
                    }
                }
            }
        } catch { }
        return res;
    }

    public static bool SetVolumeAndMute(string deviceHint, float? newVolume, bool? newMute, bool toggleMute) {
        try {
            var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
            IMMDevice dev;
            enumerator.GetDefaultAudioEndpoint(0, 1, out dev);
            Guid iidMgr = typeof(IAudioSessionManager2).GUID;
            object objMgr;
            dev.Activate(ref iidMgr, 23, IntPtr.Zero, out objMgr);
            var mgr = (IAudioSessionManager2)objMgr;
            IAudioSessionEnumerator sesEnum;
            mgr.GetSessionEnumerator(out sesEnum);
            int count;
            sesEnum.GetCount(out count);
            string hint = (deviceHint ?? "").ToLowerInvariant();
            bool updated = false;

            for (int i = 0; i < count; i++) {
                IAudioSessionControl ctrl;
                sesEnum.GetSession(i, out ctrl);
                var ctrl2 = (IAudioSessionControl2)ctrl;
                var vol = (ISimpleAudioVolume)ctrl;
                string disp, sid;
                ctrl2.GetDisplayName(out disp);
                ctrl2.GetSessionIdentifier(out sid);

                bool matchHint = !string.IsNullOrEmpty(hint) && ((disp ?? "").ToLowerInvariant().Contains(hint));
                if (IsBtSession(disp, sid) || matchHint) {
                    Guid ctx = Guid.Empty;
                    if (newVolume.HasValue) {
                        float clamped = Math.Max(0.0f, Math.Min(1.0f, newVolume.Value));
                        vol.SetMasterVolume(clamped, ref ctx);
                    }
                    if (toggleMute) {
                        bool curMute;
                        vol.GetMute(out curMute);
                        vol.SetMute(!curMute, ref ctx);
                    } else if (newMute.HasValue) {
                        vol.SetMute(newMute.Value, ref ctx);
                    }
                    updated = true;
                }
            }
            return updated;
        } catch {
            return false;
        }
    }
}
"@

$null = [Windows.Media.Audio.AudioPlaybackConnection, Windows.Media.Audio, ContentType=WindowsRuntime]
$null = [Windows.Media.Audio.AudioPlaybackConnectionOpenResult, Windows.Media.Audio, ContentType=WindowsRuntime]
$null = [Windows.Media.Audio.AudioPlaybackConnectionState, Windows.Media.Audio, ContentType=WindowsRuntime]
$null = [Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType=WindowsRuntime]
$null = [Windows.Devices.Enumeration.DeviceInformationCollection, Windows.Devices.Enumeration, ContentType=WindowsRuntime]
$null = [Windows.Devices.Radios.Radio, Windows.Devices.Radios, ContentType=WindowsRuntime]
$null = [Windows.Devices.Radios.RadioState, Windows.Devices.Radios, ContentType=WindowsRuntime]
$null = [Windows.Devices.Radios.RadioKind, Windows.Devices.Radios, ContentType=WindowsRuntime]
$null = [Windows.Devices.Radios.RadioAccessStatus, Windows.Devices.Radios, ContentType=WindowsRuntime]
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime]
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSession, Windows.Media.Control, ContentType=WindowsRuntime]
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType=WindowsRuntime]
$null = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]
$null = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]
$null = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattReadResult, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]
$null = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]
$null = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]
$null = [Windows.Devices.Bluetooth.BluetoothCacheMode, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq "AsTask" -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
})[0]

$asTaskAction = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq "AsTask" -and (-not $_.IsGenericMethod) -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq "IAsyncAction"
})[0]

$asBufferMethod = ([System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions].GetMethods() | Where-Object {
    $_.Name -eq "AsBuffer" -and $_.GetParameters().Count -eq 1
})[0]

$toArrayMethod = ([System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions].GetMethods() | Where-Object {
    $_.Name -eq "ToArray" -and $_.GetParameters().Count -eq 1
})[0]

function Await($WinRtTask, $ResultType, $TimeoutMs = 6000) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    if ($netTask.Wait($TimeoutMs)) {
        return $netTask.Result
    }
    return $null
}

function Await-Action($WinRtAction, $TimeoutMs = 6000) {
    $netTask = $asTaskAction.Invoke($null, @($WinRtAction))
    $netTask.Wait($TimeoutMs) | Out-Null
}

# Persistent state held in memory while mini-player runs
$script:activeConnection = $null
$script:activeDeviceId = ""
$script:activeDeviceName = ""
$script:smtcMgr = $null

function Get-BtRadioState {
    try {
        $radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
        foreach ($r in $radios) {
            if ($r.Kind -eq [Windows.Devices.Radios.RadioKind]::Bluetooth) {
                return $r.State.ToString()
            }
        }
        return "NotFound"
    } catch {
        return "Unknown"
    }
}

function Set-BtRadioState($enable) {
    try {
        $radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
        foreach ($r in $radios) {
            if ($r.Kind -eq [Windows.Devices.Radios.RadioKind]::Bluetooth) {
                $targetState = if ($enable) { [Windows.Devices.Radios.RadioState]::On } else { [Windows.Devices.Radios.RadioState]::Off }
                $res = Await ($r.SetStateAsync($targetState)) ([Windows.Devices.Radios.RadioAccessStatus])
                return @{ ok = $true; status = $res.ToString(); state = $r.State.ToString() }
            }
        }
        return @{ ok = $false; error = "No se encontro radio Bluetooth" }
    } catch {
        return @{ ok = $false; error = $_.Exception.Message }
    }
}

function Get-BtAudioDevices {
    $list = @()
    try {
        $selector = [Windows.Media.Audio.AudioPlaybackConnection]::GetDeviceSelector()
        $devices = Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)) ([Windows.Devices.Enumeration.DeviceInformationCollection])
        foreach ($d in $devices) {
            $list += @{
                id = $d.Id
                name = $d.Name
                isConnected = ($script:activeConnection -ne $null -and $script:activeDeviceId -eq $d.Id -and $script:activeConnection.State -eq [Windows.Media.Audio.AudioPlaybackConnectionState]::Opened)
            }
        }
    } catch {
    }
    return $list
}

function Connect-BtAudio($deviceId, $deviceName) {
    try {
        if ($script:activeConnection -ne $null) {
            try { $script:activeConnection.Dispose() } catch {}
            $script:activeConnection = $null
            $script:activeDeviceId = ""
            $script:activeDeviceName = ""
        }
        $conn = [Windows.Media.Audio.AudioPlaybackConnection]::TryCreateFromId($deviceId)
        if ($null -eq $conn) {
            return @{ ok = $false; error = "No se pudo crear AudioPlaybackConnection para este dispositivo." }
        }
        Await-Action ($conn.StartAsync())
        $openRes = Await ($conn.OpenAsync()) ([Windows.Media.Audio.AudioPlaybackConnectionOpenResult])
        $statusStr = if ($null -ne $openRes) { $openRes.Status.ToString() } else { "TimedOut" }
        if ($statusStr -eq "Success") {
            $script:activeConnection = $conn
            $script:activeDeviceId = $deviceId
            $script:activeDeviceName = $deviceName
            # Ensure unmuted when connecting
            $null = [BtAudioMixer]::SetVolumeAndMute($deviceName, $null, $false, $false)
            return @{
                ok = $true
                status = $statusStr
                state = $conn.State.ToString()
                deviceId = $deviceId
                deviceName = $deviceName
            }
        } else {
            try { $conn.Dispose() } catch {}
            return @{
                ok = $false
                status = $statusStr
                error = "No se pudo abrir el canal de audio ($statusStr). Asegurate de que el Bluetooth de tu dispositivo este encendido y conectado al PC."
            }
        }
    } catch {
        return @{ ok = $false; error = $_.Exception.Message }
    }
}

function Disconnect-BtAudio {
    try {
        if ($script:activeConnection -ne $null) {
            $script:activeConnection.Dispose()
            $script:activeConnection = $null
            $script:activeDeviceId = ""
            $script:activeDeviceName = ""
        }
        return @{ ok = $true; connected = $false }
    } catch {
        return @{ ok = $false; error = $_.Exception.Message }
    }
}

function Get-SmtcMediaInfo {
    try {
        if ($null -eq $script:smtcMgr) {
            $script:smtcMgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]) 2000
        }
        if ($null -eq $script:smtcMgr) { return @{ found = $false } }
        $sessions = @($script:smtcMgr.GetSessions())
        if ($sessions.Length -eq 0) { return @{ found = $false } }
        $target = $script:smtcMgr.GetCurrentSession()
        if ($null -eq $target) { $target = $sessions[0] }
        $props = Await ($target.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]) 1500
        if ($null -eq $props -or [string]::IsNullOrWhiteSpace($props.Title)) {
            return @{ found = $false }
        }
        $info = $target.GetPlaybackInfo()
        $statusStr = if ($null -ne $info) { $info.PlaybackStatus.ToString() } else { "Unknown" }
        return @{
            found = $true
            app = [string]$target.SourceAppUserModelId
            title = [string]$props.Title
            artist = [string]$props.Artist
            album = [string]$props.AlbumTitle
            status = $statusStr
            playing = ($statusStr -eq "Playing")
        }
    } catch {
        return @{ found = $false }
    }
}

function Invoke-SmtcMediaCommand($cmd) {
    try {
        if ($null -eq $script:smtcMgr) {
            $script:smtcMgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]) 2000
        }
        if ($null -eq $script:smtcMgr) { return @{ ok = $false } }
        $target = $script:smtcMgr.GetCurrentSession()
        if ($null -eq $target) {
            $sessions = @($script:smtcMgr.GetSessions())
            if ($sessions.Length -gt 0) { $target = $sessions[0] }
        }
        if ($null -eq $target) { return @{ ok = $false } }
        $res = $false
        switch ($cmd) {
            "play_pause" { $res = Await ($target.TryTogglePlayPauseAsync()) ([bool]) 2000 }
            "play"       { $res = Await ($target.TryPlayAsync()) ([bool]) 2000 }
            "pause"      { $res = Await ($target.TryPauseAsync()) ([bool]) 2000 }
            "next"       { $res = Await ($target.TrySkipNextAsync()) ([bool]) 2000 }
            "prev"       { $res = Await ($target.TrySkipPreviousAsync()) ([bool]) 2000 }
        }
        return @{ ok = [bool]$res }
    } catch {
        return @{ ok = $false }
    }
}

# Universal BLE Media Command (supports iOS Apple Media Service AMS + Android/BLE Generic Media Control Service GMCS 0x1849)
function Invoke-BleMediaCommand($cmd) {
    try {
        $allDevs = Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync()) ([Windows.Devices.Enumeration.DeviceInformationCollection]) 2500
        if ($null -eq $allDevs) { return @{ ok = $false } }

        # 1. Check Apple Media Service (AMS: 89d3502b-0f36-433a-8ef4-c502ad55f8dc) for iPhone / iPad
        $amsDevs = @($allDevs | Where-Object { $_.Id -match "89d3502b-0f36-433a-8ef4-c502ad55f8dc" -and $_.Id -match "6e3bb679-4372-40c8-9eaa-4509df260cd8" })
        foreach ($d in $amsDevs) {
            $svc = Await ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService]::FromIdAsync($d.Id)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService]) 2000
            if ($null -ne $svc) {
                $charsRes = Await ($svc.GetCharacteristicsAsync()) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult]) 2000
                if ($null -ne $charsRes -and $charsRes.Status.ToString() -eq "Success") {
                    # AMS Remote Command Characteristic: 9b3c81d8-57b1-4a8a-b8df-0e56f7ca51c2
                    $rc = $charsRes.Characteristics | Where-Object { $_.Uuid.ToString() -match "9b3c81d8-57b1-4a8a-b8df-0e56f7ca51c2" } | Select-Object -First 1
                    if ($null -ne $rc) {
                        $amsByte = switch ($cmd) {
                            "play"       { 0 }
                            "pause"      { 1 }
                            "play_pause" { 2 }
                            "next"       { 3 }
                            "prev"       { 4 }
                            default      { 2 }
                        }
                        $buf = $asBufferMethod.Invoke($null, @(,[byte[]]@([byte]$amsByte)))
                        $wStatus = Await ($rc.WriteValueAsync($buf, [Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption]::WriteWithResponse)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus]) 2500
                        if ($null -ne $wStatus -and $wStatus.ToString() -eq "Success") {
                            return @{ ok = $true; method = "BLE-AMS (iOS)" }
                        }
                    }
                }
            }
        }

        # 2. Check Generic Media Control Service (GMCS: 00001849 / MCS: 00001848)
        $gmcsDevs = @($allDevs | Where-Object { ($_.Id -match "00001849-0000-1000-8000-00805f9b34fb" -or $_.Id -match "00001848-0000-1000-8000-00805f9b34fb") -and $_.Id -match "6e3bb679-4372-40c8-9eaa-4509df260cd8" })
        foreach ($d in $gmcsDevs) {
            $svc = Await ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService]::FromIdAsync($d.Id)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService]) 2000
            if ($null -ne $svc) {
                $charsRes = Await ($svc.GetCharacteristicsAsync()) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult]) 2000
                if ($null -ne $charsRes -and $charsRes.Status.ToString() -eq "Success") {
                    # Media Control Point: 00002ba4
                    $mcp = $charsRes.Characteristics | Where-Object { $_.Uuid.ToString().StartsWith("00002ba4") } | Select-Object -First 1
                    if ($null -ne $mcp) {
                        $opcode = switch ($cmd) {
                            "play"       { 1 }
                            "pause"      { 2 }
                            "play_pause" { 2 }
                            "prev"       { 0x30 }
                            "next"       { 0x31 }
                            default      { 2 }
                        }
                        $buf = $asBufferMethod.Invoke($null, @(,[byte[]]@([byte]$opcode)))
                        $wStatus = Await ($mcp.WriteValueAsync($buf, [Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption]::WriteWithoutResponse)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus]) 2000
                        if ($null -ne $wStatus -and $wStatus.ToString() -eq "Success") {
                            return @{ ok = $true; method = "BLE-GMCS" }
                        }
                    }
                }
            }
        }
    } catch {}
    return @{ ok = $false }
}

function Get-FullState {
    $isConn = ($script:activeConnection -ne $null -and $script:activeConnection.State -eq [Windows.Media.Audio.AudioPlaybackConnectionState]::Opened)
    if (-not $isConn -and $script:activeConnection -ne $null) {
        try { $script:activeConnection.Dispose() } catch {}
        $script:activeConnection = $null
        $script:activeDeviceId = ""
        $script:activeDeviceName = ""
    }
    $mixer = [BtAudioMixer]::GetStatus($script:activeDeviceName)
    return @{
        ok = $true
        btRadioState = (Get-BtRadioState)
        audioConnected = [bool]$isConn
        activeDeviceId = $script:activeDeviceId
        activeDeviceName = $script:activeDeviceName
        devices = @(Get-BtAudioDevices)
        smtc = (Get-SmtcMediaInfo)
        mixer = @{
            found = $mixer.FoundSession
            sessionState = $mixer.SessionState
            sessionName = $mixer.SessionName
            volume = [Math]::Round($mixer.Volume * 100)
            muted = $mixer.Muted
            peak = [Math]::Round($mixer.Peak * 100, 1)
            teamsActive = $mixer.TeamsActive
        }
    }
}

while ($null -ne ($line = [Console]::In.ReadLine())) {
    $line = $line.Trim()
    if ([string]::IsNullOrEmpty($line)) { continue }
    try {
        $req = $line | ConvertFrom-Json
        $action = $req.action
        $resp = $null
        switch ($action) {
            "state" {
                $resp = Get-FullState
            }
            "bt_power" {
                $resp = Set-BtRadioState ([bool]$req.enable)
            }
            "connect" {
                $resp = Connect-BtAudio ([string]$req.deviceId) ([string]$req.deviceName)
            }
            "disconnect" {
                $resp = Disconnect-BtAudio
            }
            "mixer" {
                $volVal = if ($null -ne $req.volume) { [float]($req.volume / 100.0) } else { $null }
                $muteVal = if ($null -ne $req.mute) { [bool]$req.mute } else { $null }
                $toggleVal = if ($null -ne $req.toggleMute) { [bool]$req.toggleMute } else { $false }
                $ok = [BtAudioMixer]::SetVolumeAndMute($script:activeDeviceName, $volVal, $muteVal, $toggleVal)
                $mixer = [BtAudioMixer]::GetStatus($script:activeDeviceName)
                $resp = @{
                    ok = $ok
                    volume = [Math]::Round($mixer.Volume * 100)
                    muted = $mixer.Muted
                    peak = [Math]::Round($mixer.Peak * 100, 1)
                }
            }
            "open_settings" {
                Start-Process "ms-settings:bluetooth"
                $resp = @{ ok = $true }
            }
            "open_phonelink" {
                Start-Process "ms-phone:"
                $resp = @{ ok = $true }
            }
            "smtc_cmd" {
                $smtcRes = Invoke-SmtcMediaCommand ([string]$req.cmd)
                if ($smtcRes.ok) {
                    $resp = $smtcRes
                } else {
                    $resp = Invoke-BleMediaCommand ([string]$req.cmd)
                }
            }
            default {
                $resp = @{ ok = $false; error = "Unknown action: $action" }
            }
        }
        $jsonOut = $resp | ConvertTo-Json -Compress -Depth 6
        [Console]::Out.WriteLine($jsonOut)
        [Console]::Out.Flush()
    } catch {
        $errOut = @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
        [Console]::Out.WriteLine($errOut)
        [Console]::Out.Flush()
    }
}
