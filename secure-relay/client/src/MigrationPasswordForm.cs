using System;
using System.Drawing;
using System.Windows.Forms;

public sealed class MigrationPasswordForm : Form
{
    private readonly TextBox password=new TextBox(),confirm=new TextBox();
    public string MigrationPassword{get{return password.Text;}}
    public MigrationPasswordForm(bool creating)
    {
        AppBrand.Apply(this,creating?" - 导出迁移备份":" - 导入迁移备份");Font=new Font("Microsoft YaHei UI",9F);ClientSize=new Size(480,creating?230:185);FormBorderStyle=FormBorderStyle.FixedDialog;StartPosition=FormStartPosition.CenterParent;MaximizeBox=false;MinimizeBox=false;
        Label note=new Label{Text=creating?"设置至少 12 个字符的迁移密码。密码不会写入备份，请通过其他安全方式交给新电脑。":"输入旧电脑导出备份时设置的迁移密码。",Dock=DockStyle.Top,Height=55,Padding=new Padding(16,12,16,0)};
        TableLayoutPanel fields=new TableLayoutPanel{Dock=DockStyle.Top,Height=creating?95:48,ColumnCount=2,RowCount=creating?2:1,Padding=new Padding(16,0,16,0)};fields.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute,100));fields.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));password.UseSystemPasswordChar=true;confirm.UseSystemPasswordChar=true;password.Dock=DockStyle.Fill;confirm.Dock=DockStyle.Fill;fields.Controls.Add(new Label{Text="迁移密码",TextAlign=ContentAlignment.MiddleLeft,Dock=DockStyle.Fill},0,0);fields.Controls.Add(password,1,0);if(creating){fields.Controls.Add(new Label{Text="再次输入",TextAlign=ContentAlignment.MiddleLeft,Dock=DockStyle.Fill},0,1);fields.Controls.Add(confirm,1,1);}
        FlowLayoutPanel actions=new FlowLayoutPanel{Dock=DockStyle.Bottom,Height=54,FlowDirection=FlowDirection.RightToLeft,Padding=new Padding(0,8,12,0)};Button cancel=new Button{Text="取消",DialogResult=DialogResult.Cancel,AutoSize=true};Button ok=new Button{Text=creating?"加密并保存":"解密并验证",AutoSize=true};ok.Click+=delegate{if(password.Text.Length<12){MessageBox.Show(this,"迁移密码至少需要 12 个字符。","密码不符合要求",MessageBoxButtons.OK,MessageBoxIcon.Warning);return;}if(creating&&password.Text!=confirm.Text){MessageBox.Show(this,"两次输入的迁移密码不一致。","密码不一致",MessageBoxButtons.OK,MessageBoxIcon.Warning);return;}DialogResult=DialogResult.OK;Close();};actions.Controls.Add(cancel);actions.Controls.Add(ok);Controls.Add(fields);Controls.Add(note);Controls.Add(actions);CancelButton=cancel;AcceptButton=ok;
    }
}
