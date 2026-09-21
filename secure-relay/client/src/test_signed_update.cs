using System;
using System.IO;

public static class SignedUpdateTests
{
    private static int failures;private static void Check(bool value,string name){if(value)Console.WriteLine("PASS "+name);else{Console.WriteLine("FAIL "+name);failures++;}}
    private static bool Reject(Action action){try{action();return false;}catch(InvalidDataException){return true;}}
    public static int Main()
    {
        string thumb=UpdateVerificationPolicy.PublisherThumbprint,hash=new string('A',64);
        Check(!UpdateRolloutPolicy.IsEligible("machine-a",0),"zero percent rollout excludes machine");
        Check(UpdateRolloutPolicy.IsEligible("machine-a",100),"full rollout includes machine");
        bool first=UpdateRolloutPolicy.IsEligible("stable-machine",37);Check(first==UpdateRolloutPolicy.IsEligible("stable-machine",37),"rollout assignment is stable");
        int selected=0;for(int i=0;i<1000;i++)if(UpdateRolloutPolicy.IsEligible("machine-"+i,25))selected++;Check(selected>180&&selected<320,"rollout distributes machines across batches");
        bool valid=true;try{UpdateVerificationPolicy.ValidateMetadata("2.3.9","2.3.9.0",thumb,hash,hash,true);}catch{valid=false;}Check(valid,"matching version signature and hash are accepted");
        Check(Reject(delegate{UpdateVerificationPolicy.ValidateMetadata("2.3.9","2.4.0.0",thumb,hash,hash,true);}),"version mismatch is rejected");
        Check(Reject(delegate{UpdateVerificationPolicy.ValidateMetadata("2.3.9","2.3.9.0",new string('B',40),hash,hash,true);}),"unexpected publisher is rejected");
        Check(Reject(delegate{UpdateVerificationPolicy.ValidateMetadata("2.3.9","2.3.9.0",thumb,hash,new string('B',64),true);}),"checksum mismatch is rejected");
        return failures==0?0:1;
    }
}
