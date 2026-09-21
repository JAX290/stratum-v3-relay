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

public sealed class DiagnosticReportForm : Form
{
    private readonly TextBox content=new TextBox();private readonly string originalPath;
    public DiagnosticReportForm(string report,string path){originalPath=path;AppBrand.Apply(this," - 诊断报告");Font=new Font("Microsoft YaHei UI",9F);ClientSize=new Size(820,620);MinimumSize=new Size(650,450);StartPosition=FormStartPosition.CenterParent;Label note=new Label();note.Text="报告已自动保存在本机。它不包含共享密钥、证书私钥或矿池密码。";note.Dock=DockStyle.Top;note.Height=40;note.Padding=new Padding(12,11,0,0);note.BackColor=Color.FromArgb(236,244,252);content.Text=report;content.Multiline=true;content.ReadOnly=true;content.ScrollBars=ScrollBars.Both;content.WordWrap=false;content.Dock=DockStyle.Fill;content.Font=new Font("Consolas",10F);FlowLayoutPanel actions=new FlowLayoutPanel();actions.Dock=DockStyle.Bottom;actions.Height=50;actions.FlowDirection=FlowDirection.RightToLeft;actions.Padding=new Padding(0,8,12,0);Button close=new Button();close.Text="关闭";close.AutoSize=true;close.Click+=delegate{Close();};Button copy=new Button();copy.Text="复制报告";copy.AutoSize=true;copy.Click+=delegate{Clipboard.SetText(content.Text);copy.Text="已复制";};Button save=new Button();save.Text="另存为";save.AutoSize=true;save.Click+=delegate{SaveFileDialog dialog=new SaveFileDialog();dialog.Filter="文本文件|*.txt";dialog.FileName=Path.GetFileName(originalPath);if(dialog.ShowDialog(this)==DialogResult.OK)File.WriteAllText(dialog.FileName,content.Text,Encoding.UTF8);dialog.Dispose();};actions.Controls.Add(close);actions.Controls.Add(copy);actions.Controls.Add(save);Controls.Add(content);Controls.Add(note);Controls.Add(actions);}
}

