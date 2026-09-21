using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Net.NetworkInformation;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using Microsoft.Win32;

public static class CrashRecovery
{
    private static int restartScheduled;
    private static readonly string CrashLog=Path.Combine(ConfigStore.Folder,"crash.log");
    private static readonly string RestartHistory=Path.Combine(ConfigStore.Folder,"restart-history.txt");

    public static void Log(Exception error,string source)
    {
        try{Directory.CreateDirectory(ConfigStore.Folder);File.AppendAllText(CrashLog,DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")+"  "+source+Environment.NewLine+error+Environment.NewLine+Environment.NewLine,Encoding.UTF8);if(new FileInfo(CrashLog).Length>2097152){string text=File.ReadAllText(CrashLog,Encoding.UTF8);File.WriteAllText(CrashLog,text.Substring(Math.Max(0,text.Length-1048576)),Encoding.UTF8);}}catch{}
    }

    public static void Schedule(Exception error,string source)
    {
        Log(error,source);if(Interlocked.Exchange(ref restartScheduled,1)!=0)return;
        try{if(!AllowRestart()){Log(new InvalidOperationException("10分钟内已发生3次异常，停止自动重启，避免循环。"),"自动恢复保护");return;}ProcessStartInfo helper=new ProcessStartInfo(Application.ExecutablePath,"--recover "+Process.GetCurrentProcess().Id);helper.UseShellExecute=false;helper.CreateNoWindow=true;Process.Start(helper);}catch(Exception restartError){Log(restartError,"启动自动恢复失败");}
    }

    private static bool AllowRestart()
    {
        try{Directory.CreateDirectory(ConfigStore.Folder);DateTime cutoff=DateTime.UtcNow.AddMinutes(-10);List<long> ticks=new List<long>();if(File.Exists(RestartHistory))foreach(string line in File.ReadAllLines(RestartHistory)){long value;if(Int64.TryParse(line,out value)&&new DateTime(value,DateTimeKind.Utc)>=cutoff)ticks.Add(value);}if(ticks.Count>=3)return false;ticks.Add(DateTime.UtcNow.Ticks);List<string> output=new List<string>();foreach(long value in ticks)output.Add(value.ToString());File.WriteAllLines(RestartHistory,output.ToArray());return true;}catch{return true;}
    }

    public static bool RunHelper(string[] args)
    {
        if(args==null||args.Length<2||!String.Equals(args[0],"--recover",StringComparison.OrdinalIgnoreCase))return false;
        int pid;try{if(Int32.TryParse(args[1],out pid)){try{Process.GetProcessById(pid).WaitForExit(30000);}catch{}}Thread.Sleep(3000);ProcessStartInfo start=new ProcessStartInfo(Application.ExecutablePath);start.UseShellExecute=false;Process.Start(start);}catch(Exception error){Log(error,"自动恢复助手");}return true;
    }
}
