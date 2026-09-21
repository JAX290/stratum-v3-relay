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

public sealed class BackupForm : Form
{
    private readonly List<ServerEditor> editors = new List<ServerEditor>();
    public List<ServerProfile> Profiles = new List<ServerProfile>();
    public BackupForm(List<ServerProfile> profiles,RelayManager manager,string siteName)
    {
        AppBrand.Apply(this," - 备用 VPS 设置"); Font=new Font("Microsoft YaHei UI",9F); ClientSize=new Size(690,500); StartPosition=FormStartPosition.CenterParent;
        TabControl tabs=new TabControl(); tabs.Dock=DockStyle.Fill;
        for(int i=0;i<2;i++) { ServerProfile p=i<profiles.Count?profiles[i].Copy():new ServerProfile{Name="备用VPS "+(i+1),Enabled=false,Port=443}; ServerEditor editor=new ServerEditor(p,manager,siteName); editors.Add(editor); TabPage page=new TabPage("备用 VPS "+(i+1)); page.Controls.Add(editor); tabs.TabPages.Add(page); }
        FlowLayoutPanel buttons=new FlowLayoutPanel(); buttons.Dock=DockStyle.Bottom; buttons.Height=48; buttons.FlowDirection=FlowDirection.RightToLeft; buttons.Padding=new Padding(0,8,12,0);
        Button ok=new Button(); ok.Text="保存"; ok.AutoSize=true; ok.Click+=delegate { try { Profiles.Clear(); foreach(ServerEditor e in editors){ServerProfile p=e.Value(); if(p.Enabled) MainFormValidate(p); Profiles.Add(p);} DialogResult=DialogResult.OK; Close(); } catch(Exception ex){MessageBox.Show(this,ex.Message,"设置有误",MessageBoxButtons.OK,MessageBoxIcon.Warning);} };
        Button cancel=new Button(); cancel.Text="取消"; cancel.AutoSize=true; cancel.DialogResult=DialogResult.Cancel; buttons.Controls.Add(ok); buttons.Controls.Add(cancel);
        Controls.Add(tabs); Controls.Add(buttons); AcceptButton=ok; CancelButton=cancel;
    }
    private static void MainFormValidate(ServerProfile p) { if(String.IsNullOrWhiteSpace(p.Address)) throw new InvalidOperationException(p.Name+"：请填写 VPS 地址。"); if((p.SharedKey??"").Length<32||!Regex.IsMatch(p.SharedKey??"","^[A-Za-z0-9_-]+$")) throw new InvalidOperationException(p.Name+"：共享密钥格式不正确。"); if(String.IsNullOrWhiteSpace(p.CertificateSha256)&&String.IsNullOrWhiteSpace(p.ServerName)) throw new InvalidOperationException(p.Name+"：请填写证书名称或证书指纹。"); }
}

public sealed class ServerEditor : Panel
{
    private readonly CheckBox enabled=new CheckBox(); private readonly TextBox address=new TextBox(); private readonly NumericUpDown port=new NumericUpDown(); private readonly TextBox serverName=new TextBox(); private readonly TextBox pin=new TextBox(); private readonly TextBox key=new TextBox(); private readonly Button test=new Button(); private readonly Label testResult=new Label(); private readonly string profileName; private readonly RelayManager manager; private readonly string siteName;
    public ServerEditor(ServerProfile p,RelayManager relay,string mineSiteName)
    {
        profileName=p.Name;manager=relay;siteName=mineSiteName; Dock=DockStyle.Fill; AutoScroll=true; TableLayoutPanel grid=new TableLayoutPanel(); grid.Dock=DockStyle.Top; grid.AutoSize=true; grid.AutoSizeMode=AutoSizeMode.GrowAndShrink; grid.Padding=new Padding(18); grid.ColumnCount=2; grid.RowCount=7; grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,150)); grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
        enabled.Text="启用这条备用线路"; enabled.Checked=p.Enabled; enabled.AutoSize=true; Add(grid,0,"状态",enabled);
        address.Text=p.Address; Add(grid,1,"VPS 地址",address); port.Minimum=1;port.Maximum=65535;port.Value=Math.Max(1,Math.Min(65535,p.Port));Add(grid,2,"TLS 端口",port);
        serverName.Text=p.ServerName;Add(grid,3,"证书名称（可选）",serverName);pin.Text=p.CertificateSha256;Add(grid,4,"证书 SHA-256（可选）",pin);key.Text=p.SharedKey;key.UseSystemPasswordChar=true;Add(grid,5,"独立共享密钥",key);
        FlowLayoutPanel action=new FlowLayoutPanel();action.Dock=DockStyle.Fill;action.WrapContents=false;test.Text="测试这台VPS";test.AutoSize=true;test.Click+=delegate{TestConnection();};testResult.AutoSize=true;testResult.Margin=new Padding(12,9,0,0);testResult.Text="尚未检测";action.Controls.Add(test);action.Controls.Add(testResult);Add(grid,6,"连接检测",action);Controls.Add(grid);
    }
    private static void Add(TableLayoutPanel g,int row,string text,Control c){g.RowStyles.Add(new RowStyle(SizeType.Absolute,48));Label l=new Label();l.Text=text;l.Dock=DockStyle.Fill;l.TextAlign=ContentAlignment.MiddleLeft;c.Dock=DockStyle.Fill;c.Margin=new Padding(3,8,3,8);g.Controls.Add(l,0,row);g.Controls.Add(c,1,row);}
    public ServerProfile Value(){return new ServerProfile{Name=profileName,Enabled=enabled.Checked,Address=address.Text.Trim(),Port=(int)port.Value,ServerName=serverName.Text.Trim(),CertificateSha256=pin.Text.Trim(),SharedKey=key.Text.Trim()};}
    private async void TestConnection(){ServerProfile p=Value();try{if(String.IsNullOrWhiteSpace(p.Address))throw new InvalidOperationException("请填写VPS地址。");if((p.SharedKey??"").Length<32)throw new InvalidOperationException("共享密钥格式不正确。");if(String.IsNullOrWhiteSpace(p.CertificateSha256)&&String.IsNullOrWhiteSpace(p.ServerName))throw new InvalidOperationException("请填写证书名称或证书指纹。");test.Enabled=false;test.Text="测试中…";testResult.ForeColor=Color.DimGray;testResult.Text="正在验证TCP、TLS和密钥（最长约24秒）";EndpointState result=await manager.TestProfileAsync(p,siteName,CancellationToken.None);if(IsDisposed||Disposing)return;testResult.ForeColor=Color.ForestGreen;testResult.Text="正常 · "+result.LatencyMs+" ms · "+result.LastCheck.ToString("HH:mm:ss");}catch(Exception ex){if(IsDisposed||Disposing)return;testResult.ForeColor=Color.Firebrick;testResult.Text="失败 · "+ex.Message;}finally{if(!IsDisposed&&!Disposing){test.Enabled=true;test.Text="测试这台VPS";}}}
}
