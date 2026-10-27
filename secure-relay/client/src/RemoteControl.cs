using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

public sealed class RemoteClientAction
{
    public string Id="",Action="";
}

public sealed class RemoteActionReceipt
{
    public string Id="",Action="",Status="";public DateTime TimeUtc;
}

public static class RemoteActionPolicy
{
    private static readonly HashSet<string> Allowed=new HashSet<string>(StringComparer.OrdinalIgnoreCase){"diagnose","reconnect","upgrade"};
    public static Dictionary<string,string> ParseHeaders(string response)
    {
        Dictionary<string,string> headers=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);
        foreach(string line in (response??"").Split(new[]{"\r\n"},StringSplitOptions.None)){int separator=line.IndexOf(':');if(separator>0)headers[line.Substring(0,separator).Trim()]=line.Substring(separator+1).Trim();}
        return headers;
    }
    public static RemoteClientAction ParseResponse(string response)
    {
        Dictionary<string,string> headers=ParseHeaders(response);
        string action,id;if(!headers.TryGetValue("X-Client-Action",out action)||!headers.TryGetValue("X-Action-Id",out id))return null;if(!Allowed.Contains(action)||!Regex.IsMatch(id??"","^[a-f0-9]{32}$"))return null;return new RemoteClientAction{Id=id,Action=action.ToLowerInvariant()};
    }
    public static bool IsAllowed(string action){return Allowed.Contains(action??"");}
}

/// <summary>
/// 服务端下发的运行参数。客户端配置只保留"身份"（VPS 地址、证书指纹、共享密钥），
/// 端口和心跳间隔等可运营参数由服务端在每次心跳回执里给出，改一处即对全场生效。
/// 字段为 0 / null 表示服务端未下发，客户端沿用本地值。
/// </summary>
public sealed class ServerSettings
{
    public int HealthCheckMinutes;      // 0 = 未下发
    public string Ports="";             // "" = 未下发
    public static readonly string HealthHeader="X-Setting-Health-Check-Minutes", PortsHeader="X-Setting-Ports";
    public bool IsEmpty { get { return HealthCheckMinutes<1 && Ports.Length==0; } }
    public static ServerSettings Parse(string response)
    {
        ServerSettings settings=new ServerSettings();
        Dictionary<string,string> headers=RemoteActionPolicy.ParseHeaders(response);
        string value;int minutes;
        if(headers.TryGetValue(HealthHeader,out value)&&Int32.TryParse(value,out minutes)&&minutes>=1&&minutes<=1440)
            settings.HealthCheckMinutes=minutes;
        if(headers.TryGetValue(PortsHeader,out value)&&value!=null&&value.Length>0&&value.Length<=1024)
            settings.Ports=value.Trim();
        return settings;
    }
}

public sealed class ServerSettingsChange
{
    public bool HealthCheckMinutesChanged;
    public bool PortsChanged;
    public bool Changed { get { return HealthCheckMinutesChanged||PortsChanged; } }
}

public static class ServerSettingsPolicy
{
    /// <summary>
    /// 采用服务端参数。只改内存里的配置，不落盘——由调用方决定是否保存，
    /// 这样诊断、验证这类只读流程不会意外改写生产配置。
    /// 非法端口会整条丢弃，不会把中转带进无法启动的状态。
    /// </summary>
    public static ServerSettingsChange Apply(AppConfig config, ServerSettings settings)
    {
        ServerSettingsChange change=new ServerSettingsChange();
        if(config==null||settings==null)return change;
        if(settings.HealthCheckMinutes>=1&&settings.HealthCheckMinutes<=1440
            &&settings.HealthCheckMinutes!=config.HealthCheckMinutes)
        {
            config.HealthCheckMinutes=settings.HealthCheckMinutes;
            change.HealthCheckMinutesChanged=true;
        }
        if(!String.IsNullOrEmpty(settings.Ports)&&!String.Equals(settings.Ports,config.Ports,StringComparison.Ordinal))
        {
            try{PortRoute.Parse(settings.Ports);}
            catch(InvalidOperationException){return change;}
            config.Ports=settings.Ports;
            change.PortsChanged=true;
        }
        return change;
    }
}

public sealed class RemoteActionReceiptStore
{
    private readonly string path;public RemoteActionReceiptStore(string folder){path=Path.Combine(folder,"remote-action-receipt.txt");}
    public void Save(RemoteClientAction action,string status){if(action==null||!Regex.IsMatch(action.Id??"","^[a-f0-9]{32}$")||!RemoteActionPolicy.IsAllowed(action.Action)||!Regex.IsMatch(status??"","^(completed|failed|started)$"))throw new InvalidDataException("远程操作回执格式不正确。");Directory.CreateDirectory(Path.GetDirectoryName(path));string temporary=path+".tmp";File.WriteAllText(temporary,action.Id+"|"+action.Action+"|"+status+"|"+DateTime.UtcNow.Ticks,Encoding.ASCII);if(File.Exists(path))File.Replace(temporary,path,null);else File.Move(temporary,path);}
    public RemoteActionReceipt Load(){try{string[] value=File.ReadAllText(path,Encoding.ASCII).Trim().Split('|');long ticks;if(value.Length!=4||!Regex.IsMatch(value[0],"^[a-f0-9]{32}$")||!RemoteActionPolicy.IsAllowed(value[1])||!Regex.IsMatch(value[2],"^(completed|failed|started)$")||!Int64.TryParse(value[3],out ticks))return null;return new RemoteActionReceipt{Id=value[0],Action=value[1],Status=value[2],TimeUtc=new DateTime(ticks,DateTimeKind.Utc)};}catch{return null;}}
}
