using System;
using System.Collections.Generic;

public sealed class DutyHomeSummary
{
    public string Overall="";
    public int AffectedMiners;
    public string Guidance="";
    public static DutyHomeSummary Create(RelaySnapshot relay,IList<MinerSnapshot> miners)
    {
        DutyHomeSummary result=new DutyHomeSummary();int affected=0;
        if(miners!=null)foreach(MinerSnapshot miner in miners)if(miner.Connections==0||miner.Health<85)affected++;
        result.AffectedMiners=affected;
        if(relay==null||!relay.Running){result.Overall="需要处理";result.Guidance="中转未启动，请点击“检查并修复”。";return result;}
        if(!String.IsNullOrWhiteSpace(relay.RecoveryPhase)&&relay.RecoveryPhase!="已恢复"){result.Overall="正在恢复";result.Guidance=relay.RecoveryMessage;return result;}
        bool endpointProblem=false;foreach(EndpointState endpoint in relay.Endpoints)if(!endpoint.Online&&endpoint.LastCheck!=DateTime.MinValue)endpointProblem=true;
        if(endpointProblem||affected>0){result.Overall="需要关注";result.Guidance=affected>0?"有矿机离线或健康度偏低，请先检查并修复。":"有 VPS 线路异常，备用线路会自动接替。";return result;}
        result.Overall="运行正常";result.Guidance="当前不需要值守员操作。";return result;
    }
}
