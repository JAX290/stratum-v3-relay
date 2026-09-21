using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Windows.Forms;
using Microsoft.Win32.SafeHandles;

public sealed class AdminLoginForm : Form
{
    private readonly TextBox user=new TextBox(),password=new TextBox();
    public AdminLoginForm()
    {
        AppBrand.Apply(this," - 管理员解锁");Font=new Font("Microsoft YaHei UI",9F);ClientSize=new Size(430,210);FormBorderStyle=FormBorderStyle.FixedDialog;MaximizeBox=false;MinimizeBox=false;StartPosition=FormStartPosition.CenterParent;
        Label note=new Label{Text="高级设置需要 Windows 管理员身份。凭据只用于本次验证，不会保存。",AutoSize=false,Height=48,Dock=DockStyle.Top,Padding=new Padding(16,12,12,0)};
        TableLayoutPanel grid=new TableLayoutPanel{Dock=DockStyle.Top,Height=92,ColumnCount=2,RowCount=2,Padding=new Padding(16,4,16,4)};grid.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,110));grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
        user.Text=Environment.UserDomainName+"\\"+Environment.UserName;password.UseSystemPasswordChar=true;grid.Controls.Add(new Label{Text="管理员账号",Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft},0,0);grid.Controls.Add(user,1,0);grid.Controls.Add(new Label{Text="密码",Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleLeft},0,1);grid.Controls.Add(password,1,1);
        FlowLayoutPanel actions=new FlowLayoutPanel{Dock=DockStyle.Bottom,Height=54,FlowDirection=FlowDirection.RightToLeft,Padding=new Padding(0,8,12,0)};Button cancel=new Button{Text="取消",AutoSize=true,DialogResult=DialogResult.Cancel};Button unlock=new Button{Text="解锁 15 分钟",AutoSize=true};unlock.Click+=delegate{Verify();};actions.Controls.Add(cancel);actions.Controls.Add(unlock);AcceptButton=unlock;CancelButton=cancel;Controls.Add(grid);Controls.Add(note);Controls.Add(actions);
    }
    private void Verify()
    {
        try{if(!WindowsAdminVerifier.Verify(user.Text,password.Text))throw new UnauthorizedAccessException();password.Clear();DialogResult=DialogResult.OK;Close();}
        catch{password.Clear();MessageBox.Show(this,"账号密码无效，或该账号不属于 Windows 管理员组。","无法解锁",MessageBoxButtons.OK,MessageBoxIcon.Warning);}
    }
}

internal static class WindowsAdminVerifier
{
    [DllImport("advapi32.dll",SetLastError=true,CharSet=CharSet.Unicode)]private static extern bool LogonUser(string username,string domain,string password,int logonType,int provider,out SafeAccessTokenHandle token);
    public static bool Verify(string account,string password)
    {
        if(String.IsNullOrWhiteSpace(account)||String.IsNullOrEmpty(password))return false;string domain=".",name=account.Trim();int slash=name.IndexOf('\\');if(slash>0){domain=name.Substring(0,slash);name=name.Substring(slash+1);}else if(name.Contains("@"))domain=null;
        SafeAccessTokenHandle token; if(!LogonUser(name,domain,password,2,0,out token))return false;
        using(token)using(WindowsIdentity identity=new WindowsIdentity(token.DangerousGetHandle()))return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
    }
}
