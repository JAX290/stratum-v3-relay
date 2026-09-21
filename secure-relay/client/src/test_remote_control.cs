using System;
using System.IO;

public static class RemoteControlTests
{
    private static int failures;private static void Check(bool value,string name){if(value)Console.WriteLine("PASS "+name);else{Console.WriteLine("FAIL "+name);failures++;}}
    private static RemoteClientAction Parse(string action,string id){return RemoteActionPolicy.ParseResponse("HTTP/1.1 200 OK\r\nX-Client-Action: "+action+"\r\nX-Action-Id: "+id+"\r\n\r\n");}
    public static int Main()
    {
        string id="0123456789abcdef0123456789abcdef";
        Check(Parse("diagnose",id).Action=="diagnose","diagnose action is accepted");
        Check(Parse("reconnect",id).Action=="reconnect","reconnect action is accepted");
        Check(Parse("upgrade",id).Action=="upgrade","upgrade action is accepted");
        Check(Parse("shell",id)==null,"arbitrary remote command is rejected");
        Check(Parse("diagnose","bad") == null,"malformed remote action id is rejected");
        string folder=Path.Combine(Path.GetTempPath(),"mulinsen-remote-"+Guid.NewGuid().ToString("N"));try{RemoteActionReceiptStore store=new RemoteActionReceiptStore(folder);RemoteClientAction action=Parse("reconnect",id);store.Save(action,"completed");RemoteActionReceipt receipt=store.Load();Check(receipt!=null&&receipt.Id==id&&receipt.Action=="reconnect"&&receipt.Status=="completed","remote action receipt persists across restart");bool invalid=false;try{store.Save(action,"arbitrary");}catch(InvalidDataException){invalid=true;}Check(invalid,"remote action receipt status is limited");}finally{if(Directory.Exists(folder))Directory.Delete(folder,true);}
        return failures==0?0:1;
    }
}
