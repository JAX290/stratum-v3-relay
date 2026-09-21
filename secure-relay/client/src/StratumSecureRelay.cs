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

[assembly: AssemblyTitle("木林森中转")]
[assembly: AssemblyDescription("Stratum V3 TLS client for mine-site LAN relaying")]
[assembly: AssemblyCompany("Stratum V3 Relay")]
[assembly: AssemblyProduct("木林森中转")]
public static class Program
{
    [STAThread]
    public static void Main(string[] args)
    {
        if(CrashRecovery.RunHelper(args))return;
        bool created;
        using (Mutex single = new Mutex(true, "Local\\MulinSenSecureRelayV2", out created)) {
            if (!created) { MessageBox.Show("木林森中转已经在运行，请查看任务栏右下角托盘。", "木林森中转", MessageBoxButtons.OK, MessageBoxIcon.Information); return; }
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
            Application.ThreadException+=delegate(object sender,ThreadExceptionEventArgs e){CrashRecovery.Schedule(e.Exception,"界面线程异常");Application.ExitThread();};
            AppDomain.CurrentDomain.UnhandledException+=delegate(object sender,UnhandledExceptionEventArgs e){CrashRecovery.Schedule(e.ExceptionObject as Exception??new Exception("未知后台异常"),"后台线程异常");};
            TaskScheduler.UnobservedTaskException+=delegate(object sender,UnobservedTaskExceptionEventArgs e){CrashRecovery.Log(e.Exception,"已处理的后台任务异常");e.SetObserved();};
            try{Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);Application.Run(new MainForm());}
            catch(Exception error){CrashRecovery.Schedule(error,"程序主循环异常");}
        }
    }
}
