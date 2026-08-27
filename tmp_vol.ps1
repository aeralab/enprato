$ErrorActionPreference = 'Continue'
Write-Output '=== endpoint volume / mute ==='
$code = @'
using System;
using System.Runtime.InteropServices;
public static class Vol {
  [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject {}
  [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDeviceEnumerator {
    int pad1();
    int EnumAudioEndpoints(int dataFlow, int dwStateMask, out IntPtr devices);
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
  }
  [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDevice {
    int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
    int OpenPropertyStore(int stgmAccess, out IntPtr ppProperties);
    int GetId([MarshalAs(UnmanagedType.LPWStr)] out string ppstrId);
  }
  [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioEndpointVolume {
    int RegisterControlChangeNotify(IntPtr p);
    int UnregisterControlChangeNotify(IntPtr p);
    int GetChannelCount(out uint pnChannelCount);
    int SetMasterVolumeLevel(float fLevelDB, Guid pguidEventContext);
    int SetMasterVolumeLevelScalar(float fLevel, Guid pguidEventContext);
    int GetMasterVolumeLevel(out float pfLevelDB);
    int GetMasterVolumeLevelScalar(out float pfLevel);
    int SetChannelVolumeLevel(uint nChannel, float fLevelDB, Guid pguidEventContext);
    int SetChannelVolumeLevelScalar(uint nChannel, float fLevel, Guid pguidEventContext);
    int GetChannelVolumeLevel(uint nChannel, out float pfLevelDB);
    int GetChannelVolumeLevelScalar(uint nChannel, out float pfLevel);
    int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, Guid pguidEventContext);
    int GetMute(out bool pbMute);
  }
  public static string Info() {
    var en = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
    IMMDevice dev;
    en.GetDefaultAudioEndpoint(0, 0, out dev);
    string id; dev.GetId(out id);
    Guid iid = typeof(IAudioEndpointVolume).GUID;
    object o; dev.Activate(ref iid, 1, IntPtr.Zero, out o);
    var vol = (IAudioEndpointVolume)o;
    float level; bool mute;
    vol.GetMasterVolumeLevelScalar(out level);
    vol.GetMute(out mute);
    if (mute) vol.SetMute(false, Guid.Empty);
    if (level < 0.3f) vol.SetMasterVolumeLevelScalar(0.85f, Guid.Empty);
    vol.GetMasterVolumeLevelScalar(out level);
    vol.GetMute(out mute);
    return "id=" + id + " level=" + level + " mute=" + mute;
  }
}
'@
try {
  Add-Type -TypeDefinition $code -ErrorAction Stop
  Write-Output ([Vol]::Info())
} catch {
  Write-Output ("vol_err=" + $_.Exception.Message)
}

Write-Output '=== try enable USB audio / speakers ==='
Get-PnpDevice | Where-Object { $_.FriendlyName -match 'USB Audio|扬声器|Speaker|Realtek' } |
  ForEach-Object { Write-Output ("{0} | {1} | {2}" -f $_.Status, $_.Class, $_.FriendlyName) }

Write-Output '=== SoundPlayer wav ==='
$wav = 'C:\Windows\Media\Alarm01.wav'
(New-Object Media.SoundPlayer $wav).PlaySync()
Write-Output 'wav_done'
