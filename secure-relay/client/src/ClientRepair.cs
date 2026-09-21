using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

public sealed class ClientHealthIssue
{
    public string Code="",Title="",Detail="";
    public bool Repairable,RequiresAdministrator;
}

public static class ClientHealthInspector
{
    public static List<ClientHealthIssue> Inspect(AppConfig config,RelayManager manager,string executable)
    {
        List<ClientHealthIssue> issues=new List<ClientHealthIssue>();List<PortRoute> routes=null;
        try{
            if(config==null)throw new InvalidOperationException("无法读取当前设置。");
            config.Normalize();int enabled=0;foreach(ServerProfile profile in config.Servers)if(profile!=null&&profile.Enabled){enabled++;CompleteConfigurationValidator.ValidateProfile(profile);}
            if(enabled==0)throw new InvalidOperationException("至少启用一个 VPS。");routes=PortRoute.Parse(config.Ports);
        }catch(Exception ex){issues.Add(Issue("CONFIG","配置无法使用",ex.Message,true,false));return issues;}
        if(manager==null||!manager.IsRunning)issues.Add(Issue("STOPPED","中转程序未启动","矿机当前无法通过这台电脑建立新连接。",true,false));
        else if(!manager.LocalHealthCheck())issues.Add(Issue("LISTENER","本地监听异常","程序仍在运行，但一个或多个本地端口没有正常监听。",true,false));
        if(manager==null||!manager.IsRunning)foreach(PortRoute route in routes)if(!CanBind(config.ListenAddress,route.LocalPort))issues.Add(Issue("PORT","端口 "+route.LocalPort+" 被占用","不能安全结束未知程序，请先查看占用该端口的程序。",false,false));
        if(!FirewallRuleManager.HasEnabledRule(executable,routes))issues.Add(Issue("FIREWALL","Windows 防火墙缺少入站规则","局域网矿机可能无法连接这些 TCP 端口。",true,true));
        return issues;
    }

    private static bool CanBind(string address,int port)
    {
        IPAddress ip;if(!IPAddress.TryParse(address,out ip))return false;TcpListener listener=null;
        try{listener=new TcpListener(ip,port);listener.Start();return true;}catch{return false;}finally{if(listener!=null)try{listener.Stop();}catch{}}
    }
    private static ClientHealthIssue Issue(string code,string title,string detail,bool repairable,bool admin){return new ClientHealthIssue{Code=code,Title=title,Detail=detail,Repairable=repairable,RequiresAdministrator=admin};}
}

public static class FirewallRuleManager
{
    public static bool HasEnabledRule(string executable,IList<PortRoute> routes)
    {
        if(String.IsNullOrWhiteSpace(executable)||routes==null||routes.Count==0)return false;
        try{
            Type type=Type.GetTypeFromProgID("HNetCfg.FwPolicy2");if(type==null)return false;object policy=Activator.CreateInstance(type);
            object rules=type.InvokeMember("Rules",BindingFlags.GetProperty,null,policy,null);
            foreach(object rule in (IEnumerable)rules){
                Type rt=rule.GetType();bool enabled=(bool)rt.InvokeMember("Enabled",BindingFlags.GetProperty,null,rule,null);if(!enabled)continue;
                int direction=(int)rt.InvokeMember("Direction",BindingFlags.GetProperty,null,rule,null);int protocol=(int)rt.InvokeMember("Protocol",BindingFlags.GetProperty,null,rule,null);
                string app=(string)rt.InvokeMember("ApplicationName",BindingFlags.GetProperty,null,rule,null);string ports=(string)rt.InvokeMember("LocalPorts",BindingFlags.GetProperty,null,rule,null);
                if(direction==1&&protocol==6&&String.Equals(Path.GetFullPath(app??""),Path.GetFullPath(executable),StringComparison.OrdinalIgnoreCase)&&Covers(ports,routes))return true;
            }
        }catch{}
        return false;
    }
    public static void EnsureRule(string executable,IList<PortRoute> routes)
    {
        if(routes==null||routes.Count==0)throw new ArgumentException("没有可配置的监听端口。");
        List<string> ports=new List<string>();foreach(PortRoute route in routes)ports.Add(route.LocalPort.ToString());
        string args="advfirewall firewall add rule name=\"木林森中转\" dir=in action=allow program=\""+executable.Replace("\"","")+"\" protocol=TCP localport="+String.Join(",",ports.ToArray())+" profile=private enable=yes";
        ProcessStartInfo info=new ProcessStartInfo("netsh.exe",args){UseShellExecute=true,Verb="runas",WindowStyle=ProcessWindowStyle.Hidden};
        using(Process process=Process.Start(info)){process.WaitForExit();if(process.ExitCode!=0)throw new InvalidOperationException("Windows 防火墙规则未成功写入。");}
    }
    private static bool Covers(string value,IList<PortRoute> routes)
    {
        HashSet<int> found=new HashSet<int>();foreach(string item in (value??"").Split(',')){int port;if(Int32.TryParse(item.Trim(),out port))found.Add(port);}
        foreach(PortRoute route in routes)if(!found.Contains(route.LocalPort))return false;return true;
    }
}

public sealed class LatestClientRelease
{
    public string Version="",Url="",DesktopDownloadUrl="",ChecksumsDownloadUrl="";
    public int RolloutPercent=100;
    public bool IsNewerThan(string current){return CompareVersions(Version,current)>0;}
    public static int CompareVersions(string left,string right)
    {
        Version a,b;if(!System.Version.TryParse(left,out a)||!System.Version.TryParse(right,out b))return 0;return a.CompareTo(b);
    }
}

public static class LatestClientReleaseChecker
{
    public static async Task<LatestClientRelease> CheckAsync(CancellationToken cancellation)
    {
        HttpWebRequest request=(HttpWebRequest)WebRequest.Create("https://api.github.com/repos/JAX290/stratum-v3-relay/releases/latest");
        request.UserAgent="MulinSenRelay/"+AppBrand.Version;request.Accept="application/vnd.github+json";request.Timeout=5000;request.ReadWriteTimeout=5000;
        using(cancellation.Register(delegate{request.Abort();}))using(WebResponse response=await request.GetResponseAsync().ConfigureAwait(false))using(StreamReader reader=new StreamReader(response.GetResponseStream())){
            string json=await reader.ReadToEndAsync().ConfigureAwait(false);Match tag=Regex.Match(json,"\\\"tag_name\\\"\\s*:\\s*\\\"client-v([^\\\"]+)\\\"");Match url=Regex.Match(json,"\\\"html_url\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
            if(!tag.Success)throw new InvalidDataException("发布信息格式不正确。");string version=tag.Groups[1].Value;Match desktop=Regex.Match(json,"\\\"browser_download_url\\\"\\s*:\\s*\\\"([^\\\"]*/MulinSenRelay-"+Regex.Escape(version)+"\\.exe)\\\"");Match sums=Regex.Match(json,"\\\"browser_download_url\\\"\\s*:\\s*\\\"([^\\\"]*/SHA256SUMS\\.txt)\\\"");Match rollout=Regex.Match(json,"rollout\\s*:\\s*(\\d{1,3})",RegexOptions.IgnoreCase);int percent=100;if(rollout.Success)Int32.TryParse(rollout.Groups[1].Value,out percent);percent=Math.Max(0,Math.Min(100,percent));return new LatestClientRelease{Version=version,Url=url.Success?url.Groups[1].Value.Replace("\\/","/"):"",DesktopDownloadUrl=desktop.Success?desktop.Groups[1].Value.Replace("\\/","/"):"",ChecksumsDownloadUrl=sums.Success?sums.Groups[1].Value.Replace("\\/","/"):"",RolloutPercent=percent};
        }
    }
}
