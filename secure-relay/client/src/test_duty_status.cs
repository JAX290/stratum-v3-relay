using System;
using System.Collections.Generic;

public static class DutyStatusTests
{
    private static int failures;private static void Check(bool value,string name){if(value)Console.WriteLine("PASS "+name);else{Console.WriteLine("FAIL "+name);failures++;}}
    public static int Main()
    {
        DutyHomeSummary stopped=DutyHomeSummary.Create(new RelaySnapshot(),new List<MinerSnapshot>());Check(stopped.Overall=="需要处理","stopped relay is prominent");
        RelaySnapshot running=new RelaySnapshot{Running=true};DutyHomeSummary healthy=DutyHomeSummary.Create(running,new List<MinerSnapshot>());Check(healthy.Overall=="运行正常"&&healthy.AffectedMiners==0,"healthy duty summary stays concise");
        List<MinerSnapshot> miners=new List<MinerSnapshot>{new MinerSnapshot{Connections=0,Health=0},new MinerSnapshot{Connections=1,Health=70},new MinerSnapshot{Connections=1,Health=95}};
        DutyHomeSummary affected=DutyHomeSummary.Create(running,miners);Check(affected.AffectedMiners==2&&affected.Overall=="需要关注","affected miner count includes offline and unhealthy miners");
        running.RecoveryPhase="正在恢复";running.RecoveryMessage="正在重建";DutyHomeSummary recovering=DutyHomeSummary.Create(running,miners);Check(recovering.Overall=="正在恢复"&&recovering.Guidance=="正在重建","recovery progress takes priority");
        return failures==0?0:1;
    }
}
