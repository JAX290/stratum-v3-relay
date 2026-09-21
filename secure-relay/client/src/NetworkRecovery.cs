using System;
using System.Net.NetworkInformation;
using System.Threading;
using Microsoft.Win32;

public sealed class NetworkRecoveryRequest
{
    public string Reason="";
    public string Address="";
    public DateTime DueUtc;
}

public sealed class NetworkRecoveryPolicy
{
    private readonly TimeSpan stableDelay;
    private readonly TimeSpan duplicateWindow;
    private NetworkRecoveryRequest pending;
    private string lastAddress="";
    private DateTime lastCompletedUtc=DateTime.MinValue;

    public NetworkRecoveryPolicy() : this(TimeSpan.FromSeconds(5),TimeSpan.FromSeconds(30)) { }
    public NetworkRecoveryPolicy(TimeSpan delay,TimeSpan duplicate)
    {
        if(delay<TimeSpan.Zero||duplicate<TimeSpan.Zero)throw new ArgumentOutOfRangeException();
        stableDelay=delay;duplicateWindow=duplicate;
    }

    public NetworkRecoveryRequest Signal(string reason,bool available,string address,DateTime nowUtc)
    {
        ValidateUtc(nowUtc);address=(address??"").Trim();
        if(!available||address.Length==0){pending=null;return null;}
        if(String.Equals(address,lastAddress,StringComparison.OrdinalIgnoreCase)&&nowUtc-lastCompletedUtc<duplicateWindow)return null;
        pending=new NetworkRecoveryRequest{Reason=String.IsNullOrWhiteSpace(reason)?"网络状态变化":reason,Address=address,DueUtc=nowUtc+stableDelay};
        return Copy(pending);
    }

    public NetworkRecoveryRequest TryBegin(bool available,string address,DateTime nowUtc)
    {
        ValidateUtc(nowUtc);address=(address??"").Trim();
        if(pending==null||!available||address.Length==0||nowUtc<pending.DueUtc)return null;
        NetworkRecoveryRequest result=Copy(pending);result.Address=address;pending=null;return result;
    }

    public void Complete(string address,DateTime nowUtc){ValidateUtc(nowUtc);lastAddress=(address??"").Trim();lastCompletedUtc=nowUtc;pending=null;}
    public NetworkRecoveryRequest Retry(string reason,string address,DateTime nowUtc,TimeSpan delay)
    {
        ValidateUtc(nowUtc);if(delay<TimeSpan.Zero)throw new ArgumentOutOfRangeException("delay");
        pending=new NetworkRecoveryRequest{Reason=reason??"恢复重试",Address=address??"",DueUtc=nowUtc+delay};return Copy(pending);
    }
    private static NetworkRecoveryRequest Copy(NetworkRecoveryRequest value){return value==null?null:new NetworkRecoveryRequest{Reason=value.Reason,Address=value.Address,DueUtc=value.DueUtc};}
    private static void ValidateUtc(DateTime value){if(value.Kind!=DateTimeKind.Utc)throw new ArgumentException("恢复策略时间必须使用 UTC。");}
}

public sealed class NetworkRecoveryMonitor : IDisposable
{
    private readonly object gate=new object();
    private readonly Func<bool> shouldRecover;
    private readonly Func<string> currentAddress;
    private readonly Action<string> recover;
    private readonly Action<string,string> progress;
    private readonly NetworkRecoveryPolicy policy=new NetworkRecoveryPolicy();
    private readonly Timer timer;
    private bool disposed,busy,systemEventsSubscribed;
    private int attempts;

    public NetworkRecoveryMonitor(Func<bool> active,Func<string> address,Action<string> recovery,Action<string,string> report)
    {
        if(active==null)throw new ArgumentNullException("active");if(address==null)throw new ArgumentNullException("address");
        if(recovery==null)throw new ArgumentNullException("recovery");
        shouldRecover=active;currentAddress=address;recover=recovery;progress=report??delegate{};
        NetworkChange.NetworkAvailabilityChanged+=OnAvailabilityChanged;
        NetworkChange.NetworkAddressChanged+=OnAddressChanged;
        try{SystemEvents.PowerModeChanged+=OnPowerModeChanged;systemEventsSubscribed=true;}catch{}
        timer=new Timer(Tick,null,1000,1000);
    }

    public void Simulate(string reason,bool available,string address){Schedule(reason,available,address);}
    private void OnAvailabilityChanged(object sender,NetworkAvailabilityEventArgs args){Schedule(args.IsAvailable?"网络已恢复":"网络已断开",args.IsAvailable,currentAddress());}
    private void OnAddressChanged(object sender,EventArgs args){Schedule("网卡或局域网 IP 已变化",NetworkInterface.GetIsNetworkAvailable(),currentAddress());}
    private void OnPowerModeChanged(object sender,PowerModeChangedEventArgs args){if(args.Mode==PowerModes.Resume)Schedule("电脑已从休眠中唤醒",NetworkInterface.GetIsNetworkAvailable(),currentAddress());}
    private void Schedule(string reason,bool available,string address)
    {
        lock(gate){if(disposed)return;attempts=0;NetworkRecoveryRequest request=policy.Signal(reason,available,address,DateTime.UtcNow);if(!available||String.IsNullOrWhiteSpace(address))progress("等待网络",reason+"，等待可用的局域网连接。");else if(request!=null)progress("等待稳定",reason+"，确认网络稳定后自动重建中转。");}
    }
    private void Tick(object ignored)
    {
        NetworkRecoveryRequest request;
        lock(gate){if(disposed||busy||!shouldRecover())return;request=policy.TryBegin(NetworkInterface.GetIsNetworkAvailable(),currentAddress(),DateTime.UtcNow);if(request==null)return;busy=true;}
        try{
            progress("正在恢复",request.Reason+"，正在重建本地监听和 VPS 连接。");recover(request.Reason);
            lock(gate){policy.Complete(currentAddress(),DateTime.UtcNow);attempts=0;}
            progress("已恢复",request.Reason+"后的中转重建已完成。");
        }catch(Exception ex){
            lock(gate){attempts++;if(attempts<3)policy.Retry(request.Reason,currentAddress(),DateTime.UtcNow,TimeSpan.FromSeconds(10*attempts));}
            progress(attempts<3?"等待重试":"需要人工",attempts<3?"自动恢复未完成，将再次尝试。":"自动恢复连续失败，请运行一键诊断。"+SafeMessage(ex));
        }finally{lock(gate)busy=false;}
    }
    private static string SafeMessage(Exception ex){string value=ex==null?"":ex.Message;return value.Length==0?"":" 原因："+(value.Length>160?value.Substring(0,160):value);}
    public void Dispose()
    {
        lock(gate){if(disposed)return;disposed=true;}
        NetworkChange.NetworkAvailabilityChanged-=OnAvailabilityChanged;NetworkChange.NetworkAddressChanged-=OnAddressChanged;
        if(systemEventsSubscribed)try{SystemEvents.PowerModeChanged-=OnPowerModeChanged;}catch{}
        timer.Dispose();
    }
}
