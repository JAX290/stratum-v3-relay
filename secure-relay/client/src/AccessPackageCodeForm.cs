using System;
using System.Drawing;
using System.Windows.Forms;

public sealed class AccessPackageCodeForm : Form
{
    private readonly TextBox code=new TextBox();public string ImportCode{get{return code.Text;}}
    public AccessPackageCodeForm(){AppBrand.Apply(this," - 导入接入文件");Font=new Font("Microsoft YaHei UI",9F);ClientSize=new Size(460,170);FormBorderStyle=FormBorderStyle.FixedDialog;StartPosition=FormStartPosition.CenterParent;MaximizeBox=false;MinimizeBox=false;Label note=new Label{Text="输入 VPS 页面生成文件时显示的 4 组导入口令。",Dock=DockStyle.Top,Height=48,Padding=new Padding(16,14,0,0)};code.Dock=DockStyle.Top;code.Margin=new Padding(16);code.Font=new Font("Consolas",12F);FlowLayoutPanel actions=new FlowLayoutPanel{Dock=DockStyle.Bottom,Height=54,FlowDirection=FlowDirection.RightToLeft,Padding=new Padding(0,8,12,0)};Button cancel=new Button{Text="取消",DialogResult=DialogResult.Cancel,AutoSize=true};Button ok=new Button{Text="解密并验证",DialogResult=DialogResult.OK,AutoSize=true};actions.Controls.Add(cancel);actions.Controls.Add(ok);Controls.Add(code);Controls.Add(note);Controls.Add(actions);AcceptButton=ok;CancelButton=cancel;}
}
