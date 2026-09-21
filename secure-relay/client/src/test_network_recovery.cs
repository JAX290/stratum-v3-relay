using System;

public static class NetworkRecoveryTests
{
    private static int failures;
    private static void Check(bool value,string name){if(value)Console.WriteLine("PASS "+name);else{Console.WriteLine("FAIL "+name);failures++;}}
    public static int Main()
    {
        DateTime start=new DateTime(2026,9,21,0,0,0,DateTimeKind.Utc);
        NetworkRecoveryPolicy policy=new NetworkRecoveryPolicy(TimeSpan.FromSeconds(5),TimeSpan.FromSeconds(30));
        Check(policy.Signal("断网",false,"",start)==null,"offline state waits without restarting");
        NetworkRecoveryRequest request=policy.Signal("网络恢复",true,"192.168.1.20",start.AddSeconds(1));
        Check(request!=null&&request.DueUtc==start.AddSeconds(6),"recovery waits for a stable network window");
        Check(policy.TryBegin(true,"192.168.1.20",start.AddSeconds(5))==null,"recovery does not start before stability delay");
        request=policy.TryBegin(true,"192.168.1.20",start.AddSeconds(6));
        Check(request!=null&&request.Address=="192.168.1.20","stable network begins recovery");
        policy.Complete(request.Address,start.AddSeconds(7));
        Check(policy.Signal("重复地址事件",true,"192.168.1.20",start.AddSeconds(20))==null,"duplicate events after recovery are suppressed");
        Check(policy.Signal("网卡切换",true,"192.168.2.30",start.AddSeconds(21))!=null,"LAN address change schedules a rebuild");
        request=policy.TryBegin(true,"192.168.2.30",start.AddSeconds(26));
        Check(request!=null&&request.Reason=="网卡切换","address-change reason is preserved");
        policy.Retry(request.Reason,request.Address,start.AddSeconds(27),TimeSpan.FromSeconds(10));
        Check(policy.TryBegin(true,request.Address,start.AddSeconds(36))==null,"failed recovery retry is delayed");
        Check(policy.TryBegin(true,request.Address,start.AddSeconds(37))!=null,"failed recovery retry becomes eligible");
        Check(policy.Signal("休眠唤醒",true,"192.168.2.30",start.AddSeconds(70))!=null,"resume event schedules recovery after dedup window");
        return failures==0?0:1;
    }
}
