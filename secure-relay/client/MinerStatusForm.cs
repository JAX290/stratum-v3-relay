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

public sealed class MinerStatusForm : Form
{
    private readonly RelayManager manager; private readonly DataGridView grid=new DataGridView(); private readonly Label summary=new Label();
    public MinerStatusForm(RelayManager relay)
    {
        manager=relay;AppBrand.Apply(this," - 矿机状态");Font=new Font("Microsoft YaHei UI",9F);ClientSize=new Size(1450,620);MinimumSize=new Size(980,500);StartPosition=FormStartPosition.CenterParent;
        summary.Dock=DockStyle.Top;summary.Height=42;summary.Padding=new Padding(12,11,0,0);summary.BackColor=Color.FromArgb(236,244,252);Controls.Add(summary);
        FlowLayoutPanel actions=new FlowLayoutPanel();actions.Dock=DockStyle.Top;actions.Height=43;actions.Padding=new Padding(10,6,0,0);Button refresh=new Button();refresh.Text="刷新";refresh.AutoSize=true;refresh.Click+=delegate{RefreshRows();};Button remove=new Button();remove.Text="删除选中记录";remove.AutoSize=true;remove.Click+=delegate{RemoveSelected();};Button prune=new Button();prune.Text="清理离线超过24小时";prune.AutoSize=true;prune.Click+=delegate{int count=manager.RemoveExpiredMiners(TimeSpan.FromHours(24));RefreshRows();MessageBox.Show(this,"已清理 "+count+" 条记录。","清理完成",MessageBoxButtons.OK,MessageBoxIcon.Information);};actions.Controls.Add(refresh);actions.Controls.Add(remove);actions.Controls.Add(prune);Controls.Add(actions);actions.BringToFront();
        grid.Dock=DockStyle.Fill;grid.ReadOnly=true;grid.AllowUserToAddRows=false;grid.AllowUserToDeleteRows=false;grid.AutoSizeRowsMode=DataGridViewAutoSizeRowsMode.AllCells;grid.SelectionMode=DataGridViewSelectionMode.FullRowSelect;grid.RowHeadersVisible=false;grid.BackgroundColor=Color.White;grid.AutoGenerateColumns=false;
        Add("IP","矿机 IP",110);Add("Health","健康度",80);Add("Connections","连接",55);Add("Worker","矿工名",165);Add("Agent","矿机软件/型号",145);Add("Endpoint","线路",75);Add("Ports","本地端口",90);Add("LastActivity","最近活动",125);Add("Shares","提交/接受/拒绝",115);Add("Reject","拒绝率",65);Add("Latency","响应",65);Add("Hash10","10分钟估算算力",115);Add("Hash1","1小时估算算力",115);Add("Hash24","24小时估算算力",115);Add("Traffic","流量 上/下",110);Add("Disconnects","断线/失败",75);
        grid.CellDoubleClick+=delegate(object sender,DataGridViewCellEventArgs e){if(e.RowIndex>=0&&grid.Rows[e.RowIndex].Tag is MinerSnapshot)ShowDetail((MinerSnapshot)grid.Rows[e.RowIndex].Tag);};
        grid.SortCompare+=SortCompare;Controls.Add(grid);grid.BringToFront();RefreshRows();
    }
    private void RemoveSelected(){if(grid.SelectedRows.Count==0){MessageBox.Show(this,"请先选择一台矿机。","提示",MessageBoxButtons.OK,MessageBoxIcon.Information);return;}MinerSnapshot m=grid.SelectedRows[0].Tag as MinerSnapshot;if(m==null)return;if(m.Connections>0){MessageBox.Show(this,"这台矿机仍在连接，不能删除。请先确认旧 IP 已经离线。","无法删除",MessageBoxButtons.OK,MessageBoxIcon.Warning);return;}if(MessageBox.Show(this,"确定删除矿机 "+m.Ip+" 的历史记录吗？","删除记录",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;if(!manager.RemoveMiner(m.Ip)){MessageBox.Show(this,"矿机刚刚重新连接，记录没有删除。","无法删除",MessageBoxButtons.OK,MessageBoxIcon.Warning);return;}RefreshRows();}
    private void Add(string name,string title,int width){grid.Columns.Add(new DataGridViewTextBoxColumn{Name=name,HeaderText=title,Width=width,SortMode=DataGridViewColumnSortMode.Automatic});}
    private void RefreshRows()
    {
        DataGridViewColumn sorted=grid.SortedColumn;SortOrder order=grid.SortOrder;List<MinerSnapshot> items=manager.MinerSnapshots();int online=0,warn=0;grid.Rows.Clear();foreach(MinerSnapshot m in items){if(m.Connections>0)online++;if(m.Health>0&&m.Health<85)warn++;int index=grid.Rows.Add(m.Ip,m.HealthText+" "+m.Health,m.Connections,m.Worker,m.Agent,m.Endpoint,m.Ports,m.LastActivity.ToString("MM-dd HH:mm:ss"),m.Submitted+" / "+m.Accepted+" / "+m.Rejected,m.RejectPercent.ToString("0.00")+"%",m.LatencyMs+" ms",FormatHashrate(m.Hashrate10m),FormatHashrate(m.Hashrate1h),FormatHashrate(m.Hashrate24h),FormatBytes(m.Uploaded)+" / "+FormatBytes(m.Downloaded),m.Disconnects+" / "+m.Failures);DataGridViewRow row=grid.Rows[index];row.Tag=m;if(m.Connections==0)row.DefaultCellStyle.ForeColor=Color.Gray;else if(m.Health<60)row.DefaultCellStyle.BackColor=Color.MistyRose;else if(m.Health<85)row.DefaultCellStyle.BackColor=Color.LemonChiffon;}if(sorted!=null&&order!=SortOrder.None)grid.Sort(sorted,order==SortOrder.Ascending?ListSortDirection.Ascending:ListSortDirection.Descending);
        summary.Text="识别矿机："+items.Count+"    当前在线："+online+"    需要注意："+warn+"    相同 IP 已合并；点击刷新获取最新信息，点击表头排序。";
    }
    private void SortCompare(object sender,DataGridViewSortCompareEventArgs e){MinerSnapshot a=grid.Rows[e.RowIndex1].Tag as MinerSnapshot,b=grid.Rows[e.RowIndex2].Tag as MinerSnapshot;if(a==null||b==null)return;IComparable left=SortValue(e.Column.Name,a),right=SortValue(e.Column.Name,b);e.SortResult=left.CompareTo(right);e.Handled=true;}
    private static IComparable SortValue(string column,MinerSnapshot m){switch(column){case"IP":return IpNumber(m.Ip);case"Health":return m.Health;case"Connections":return m.Connections;case"LastActivity":return m.LastActivity;case"Shares":return m.Submitted;case"Reject":return m.RejectPercent;case"Latency":return m.LatencyMs;case"Hash10":return m.Hashrate10m;case"Hash1":return m.Hashrate1h;case"Hash24":return m.Hashrate24h;case"Traffic":return m.Uploaded+m.Downloaded;case"Disconnects":return m.Disconnects+m.Failures;case"Worker":return m.Worker??"";case"Agent":return m.Agent??"";case"Endpoint":return m.Endpoint??"";case"Ports":return m.Ports??"";default:return "";}}
    private static long IpNumber(string value){IPAddress ip;if(!IPAddress.TryParse(value,out ip))return Int64.MaxValue;byte[] b=ip.GetAddressBytes();if(b.Length!=4)return Int64.MaxValue;return ((long)b[0]<<24)|((long)b[1]<<16)|((long)b[2]<<8)|b[3];}
    private void ShowDetail(MinerSnapshot m){string accepted=m.LastAccepted==DateTime.MinValue?"尚未接受 Share":m.LastAccepted.ToString("yyyy-MM-dd HH:mm:ss");string message="矿机 IP："+m.Ip+"\r\n健康度："+m.HealthText+" "+m.Health+"\r\n当前连接："+m.Connections+"\r\n矿工名："+(m.Worker.Length==0?"尚未识别":m.Worker)+"\r\n矿机软件/型号："+(m.Agent.Length==0?"尚未识别":m.Agent)+"\r\n线路与端口："+m.Endpoint+" / "+m.Ports+"\r\n首次连接："+m.FirstSeen.ToString("yyyy-MM-dd HH:mm:ss")+"\r\n最近活动："+m.LastActivity.ToString("yyyy-MM-dd HH:mm:ss")+"\r\n最近接受："+accepted+"\r\n断线 / 失败："+m.Disconnects+" / "+m.Failures+"\r\n最近错误："+(m.LastError.Length==0?"无":m.LastError);MessageBox.Show(this,message,"矿机检修详情",MessageBoxButtons.OK,MessageBoxIcon.Information);}
    private static string FormatBytes(long value){string[]u={"B","KB","MB","GB","TB"};double n=value;int i=0;while(n>=1024&&i<u.Length-1){n/=1024;i++;}return n.ToString(i==0?"0":"0.0")+u[i];}
    private static string FormatHashrate(double value){string[]u={"H/s","KH/s","MH/s","GH/s","TH/s","PH/s","EH/s"};int i=0;while(value>=1000&&i<u.Length-1){value/=1000;i++;}return value<=0?"--":value.ToString(value>=100?"0":value>=10?"0.0":"0.00")+" "+u[i];}
}

