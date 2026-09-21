using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

public static class UpdateRolloutPolicy
{
    public static bool IsEligible(string machineIdentity,int percent)
    {
        if(percent<=0)return false;if(percent>=100)return true;byte[] digest;using(SHA256 sha=SHA256.Create())digest=sha.ComputeHash(Encoding.UTF8.GetBytes(machineIdentity??""));uint value=BitConverter.ToUInt32(digest,0);return value%100<(uint)percent;
    }
}

public static class UpdateVerificationPolicy
{
    public const string PublisherThumbprint="D83C5C3E10822B6A4B45333EF41AFCC4F9156AAB";
    public static void ValidateMetadata(string expectedVersion,string actualVersion,string signerThumbprint,string expectedHash,string actualHash,bool signatureTrusted)
    {
        Version expected,actual;if(!Version.TryParse(expectedVersion,out expected)||!Version.TryParse(actualVersion,out actual)||expected.Major!=actual.Major||expected.Minor!=actual.Minor||expected.Build!=actual.Build)throw new InvalidDataException("升级文件版本与发布版本不一致。");
        if(!signatureTrusted||!String.Equals(Normalize(signerThumbprint),PublisherThumbprint,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("升级文件的发布者签名无效或不是固定发布者。");
        if(!Regex.IsMatch(expectedHash??"","^[A-Fa-f0-9]{64}$")||!String.Equals(expectedHash,actualHash,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("升级文件 SHA-256 与发布清单不一致。");
    }
    private static string Normalize(string value){return Regex.Replace(value??"","\\s","").ToUpperInvariant();}
}

public static class SignedUpdatePackage
{
    public static async Task<string> DownloadAndVerifyAsync(LatestClientRelease release,string updateDirectory,CancellationToken cancellation)
    {
        if(release==null||String.IsNullOrWhiteSpace(release.DesktopDownloadUrl)||String.IsNullOrWhiteSpace(release.ChecksumsDownloadUrl))throw new InvalidDataException("发布页缺少桌面升级文件或摘要清单。");Directory.CreateDirectory(updateDirectory);string staged=Path.Combine(updateDirectory,"MulinSenRelay-"+release.Version+".staged.exe");string partial=staged+".download";if(File.Exists(partial))File.Delete(partial);string sums=await DownloadTextAsync(release.ChecksumsDownloadUrl,cancellation).ConfigureAwait(false);Match hash=Regex.Match(sums,"(?im)^([a-f0-9]{64})\\s+MulinSenRelay-"+Regex.Escape(release.Version)+"\\.exe\\s*$");if(!hash.Success)throw new InvalidDataException("发布摘要清单缺少当前桌面升级文件。");await DownloadFileAsync(release.DesktopDownloadUrl,partial,cancellation).ConfigureAwait(false);try{Verify(partial,release.Version,hash.Groups[1].Value);if(File.Exists(staged))File.Delete(staged);File.Move(partial,staged);return staged;}finally{if(File.Exists(partial))try{File.Delete(partial);}catch{}}
    }
    public static void Verify(string path,string expectedVersion,string expectedHash)
    {
        if(!File.Exists(path)||new FileInfo(path).Length<65536)throw new InvalidDataException("升级文件缺失或大小异常。");string actualHash=ComputeSha256(path);string signer="";try{signer=new X509Certificate2(X509Certificate.CreateFromSignedFile(path)).Thumbprint;}catch{}string actualVersion=FileVersionInfo.GetVersionInfo(path).FileVersion??"";UpdateVerificationPolicy.ValidateMetadata(expectedVersion,actualVersion,signer,expectedHash,actualHash,AuthenticodeTrust.IsTrusted(path));
    }
    public static string ComputeSha256(string path){using(SHA256 sha=SHA256.Create())using(FileStream stream=File.OpenRead(path))return ToHex(sha.ComputeHash(stream));}
    private static async Task<string> DownloadTextAsync(string url,CancellationToken cancellation){HttpWebRequest request=CreateRequest(url);using(cancellation.Register(delegate{request.Abort();}))using(WebResponse response=await request.GetResponseAsync().ConfigureAwait(false))using(StreamReader reader=new StreamReader(response.GetResponseStream(),Encoding.UTF8))return await reader.ReadToEndAsync().ConfigureAwait(false);}
    private static async Task DownloadFileAsync(string url,string path,CancellationToken cancellation){HttpWebRequest request=CreateRequest(url);using(cancellation.Register(delegate{request.Abort();}))using(WebResponse response=await request.GetResponseAsync().ConfigureAwait(false))using(Stream input=response.GetResponseStream())using(FileStream output=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.None)){await input.CopyToAsync(output,81920,cancellation).ConfigureAwait(false);output.Flush(true);}}
    private static HttpWebRequest CreateRequest(string url){Uri uri;if(!Uri.TryCreate(url,UriKind.Absolute,out uri)||uri.Scheme!=Uri.UriSchemeHttps||!String.Equals(uri.Host,"github.com",StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("升级下载地址不是受信任的 GitHub HTTPS 地址。");HttpWebRequest request=(HttpWebRequest)WebRequest.Create(uri);request.UserAgent="MulinSenRelay/"+AppBrand.Version;request.Timeout=15000;request.ReadWriteTimeout=15000;return request;}
    private static string ToHex(byte[] value){StringBuilder text=new StringBuilder(value.Length*2);foreach(byte item in value)text.Append(item.ToString("X2"));return text.ToString();}
}

public static class SignedUpdateCoordinator
{
    public static void Begin(string currentExecutable,string stagedExecutable,string version,string expectedHash)
    {
        string folder=Path.Combine(ConfigStore.Folder,"updates");Directory.CreateDirectory(folder);string helper=Path.Combine(folder,"update-helper-"+Guid.NewGuid().ToString("N")+".exe"),backup=Path.Combine(folder,"previous.exe"),marker=Path.Combine(folder,"startup-"+Guid.NewGuid().ToString("N")+".ok");File.Copy(currentExecutable,helper,true);string arguments="--apply-update "+Process.GetCurrentProcess().Id+" "+Quote(currentExecutable)+" "+Quote(stagedExecutable)+" "+Quote(version)+" "+Quote(expectedHash)+" "+Quote(backup)+" "+Quote(marker);Process.Start(new ProcessStartInfo(helper,arguments){UseShellExecute=false,CreateNoWindow=true});
    }
    public static bool RunHelper(string[] args)
    {
        if(args==null||args.Length<1||!String.Equals(args[0],"--apply-update",StringComparison.OrdinalIgnoreCase))return false;try{if(args.Length!=8)throw new InvalidDataException("升级助手参数不完整。");int parent;if(!Int32.TryParse(args[1],out parent))throw new InvalidDataException("升级助手进程编号无效。");try{Process.GetProcessById(parent).WaitForExit(60000);}catch{}string target=Path.GetFullPath(args[2]),staged=Path.GetFullPath(args[3]),version=args[4],hash=args[5],backup=Path.GetFullPath(args[6]),marker=Path.GetFullPath(args[7]);SignedUpdatePackage.Verify(staged,version,hash);File.Copy(target,backup,true);try{File.Copy(staged,target,true);Process child=Process.Start(new ProcessStartInfo(target,"--updated "+Quote(marker)){UseShellExecute=false});bool healthy=false;for(int i=0;i<120&&!healthy;i++){if(child.HasExited)break;healthy=File.Exists(marker);if(!healthy)Thread.Sleep(500);}if(!healthy){try{if(!child.HasExited)child.Kill();}catch{}File.Copy(backup,target,true);Process.Start(new ProcessStartInfo(target,"--update-rollback"){UseShellExecute=false});}else{try{File.Delete(staged);}catch{}try{File.Delete(marker);}catch{}}}catch{File.Copy(backup,target,true);throw;}}catch(Exception error){try{File.AppendAllText(Path.Combine(ConfigStore.Folder,"update-error.log"),DateTime.Now.ToString("s")+" "+error+Environment.NewLine,Encoding.UTF8);}catch{}}return true;
    }
    public static void SignalStartupHealthy(string[] args){if(args==null||args.Length!=2||!String.Equals(args[0],"--updated",StringComparison.OrdinalIgnoreCase))return;try{string marker=Path.GetFullPath(args[1]);Directory.CreateDirectory(Path.GetDirectoryName(marker));File.WriteAllText(marker,DateTime.UtcNow.ToString("O"),Encoding.ASCII);}catch{}}
    public static bool WasRolledBack(string[] args){return args!=null&&args.Length>0&&String.Equals(args[0],"--update-rollback",StringComparison.OrdinalIgnoreCase);}
    private static string Quote(string value){return "\\\""+(value??"").Replace("\\\"","\\\\\\\"")+"\\\"";}
}

internal static class AuthenticodeTrust
{
    private static readonly Guid Action=new Guid("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");
    [DllImport("wintrust.dll",ExactSpelling=true,SetLastError=true,CharSet=CharSet.Unicode)]private static extern int WinVerifyTrust(IntPtr hwnd,[MarshalAs(UnmanagedType.LPStruct)]Guid action,WinTrustData data);
    public static bool IsTrusted(string path){using(WinTrustFile file=new WinTrustFile(path))using(WinTrustData data=new WinTrustData(file)){return WinVerifyTrust(IntPtr.Zero,Action,data)==0;}}
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]private sealed class WinTrustFile:IDisposable{public uint cbStruct=(uint)Marshal.SizeOf(typeof(WinTrustFile));public IntPtr pcwszFilePath;public IntPtr hFile=IntPtr.Zero;public IntPtr pgKnownSubject=IntPtr.Zero;public WinTrustFile(string path){pcwszFilePath=Marshal.StringToCoTaskMemUni(path);}public void Dispose(){if(pcwszFilePath!=IntPtr.Zero)Marshal.FreeCoTaskMem(pcwszFilePath);}}
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]private sealed class WinTrustData:IDisposable{public uint cbStruct=(uint)Marshal.SizeOf(typeof(WinTrustData));public IntPtr pPolicyCallbackData=IntPtr.Zero,pSIPClientData=IntPtr.Zero;public uint dwUIChoice=2,dwRevocationChecks=0,dwUnionChoice=1;public IntPtr pFile;public uint dwStateAction=0;public IntPtr hWVTStateData=IntPtr.Zero,pwszURLReference=IntPtr.Zero;public uint dwProvFlags=0,dwUIContext=0;public WinTrustData(WinTrustFile file){pFile=Marshal.AllocCoTaskMem(Marshal.SizeOf(typeof(WinTrustFile)));Marshal.StructureToPtr(file,pFile,false);}public void Dispose(){if(pFile!=IntPtr.Zero){Marshal.DestroyStructure(pFile,typeof(WinTrustFile));Marshal.FreeCoTaskMem(pFile);}}}
}
