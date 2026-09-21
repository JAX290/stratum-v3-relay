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
    public static RemoteClientAction ParseResponse(string response)
    {
        Dictionary<string,string> headers=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);foreach(string line in (response??"").Split(new[]{"\r\n"},StringSplitOptions.None)){int separator=line.IndexOf(':');if(separator>0)headers[line.Substring(0,separator).Trim()]=line.Substring(separator+1).Trim();}
        string action,id;if(!headers.TryGetValue("X-Client-Action",out action)||!headers.TryGetValue("X-Action-Id",out id))return null;if(!Allowed.Contains(action)||!Regex.IsMatch(id??"","^[a-f0-9]{32}$"))return null;return new RemoteClientAction{Id=id,Action=action.ToLowerInvariant()};
    }
    public static bool IsAllowed(string action){return Allowed.Contains(action??"");}
}

public sealed class RemoteActionReceiptStore
{
    private readonly string path;public RemoteActionReceiptStore(string folder){path=Path.Combine(folder,"remote-action-receipt.txt");}
    public void Save(RemoteClientAction action,string status){if(action==null||!Regex.IsMatch(action.Id??"","^[a-f0-9]{32}$")||!RemoteActionPolicy.IsAllowed(action.Action)||!Regex.IsMatch(status??"","^(completed|failed|started)$"))throw new InvalidDataException("远程操作回执格式不正确。");Directory.CreateDirectory(Path.GetDirectoryName(path));string temporary=path+".tmp";File.WriteAllText(temporary,action.Id+"|"+action.Action+"|"+status+"|"+DateTime.UtcNow.Ticks,Encoding.ASCII);if(File.Exists(path))File.Replace(temporary,path,null);else File.Move(temporary,path);}
    public RemoteActionReceipt Load(){try{string[] value=File.ReadAllText(path,Encoding.ASCII).Trim().Split('|');long ticks;if(value.Length!=4||!Regex.IsMatch(value[0],"^[a-f0-9]{32}$")||!RemoteActionPolicy.IsAllowed(value[1])||!Regex.IsMatch(value[2],"^(completed|failed|started)$")||!Int64.TryParse(value[3],out ticks))return null;return new RemoteActionReceipt{Id=value[0],Action=value[1],Status=value[2],TimeUtc=new DateTime(ticks,DateTimeKind.Utc)};}catch{return null;}}
}
