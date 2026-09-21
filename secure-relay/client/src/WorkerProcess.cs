using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Win32.SafeHandles;

public interface IRelayWorker : IDisposable
{
    bool Alive { get; }
    DateTime StartedUtc { get; }
    DateTime? HeartbeatUtc { get; }
    void Stop();
}

// This handle is never inherited. If the supervisor dies, Windows kills its worker.
internal sealed class WorkerJob : SafeHandleZeroOrMinusOneIsInvalid
{
    [StructLayout(LayoutKind.Sequential)] private struct BasicLimits
    {
        public long ProcessTime,JobTime;
        public uint Flags;
        public UIntPtr MinWorkingSet,MaxWorkingSet;
        public uint ActiveProcesses;
        public UIntPtr Affinity;
        public uint Priority,Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong A,B,C,D,E,F; }
    [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimits
    {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemory,JobMemory,PeakProcessMemory,PeakJobMemory;
    }
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] private static extern IntPtr CreateJobObject(IntPtr attributes,string name);
    [DllImport("kernel32.dll",SetLastError=true)] private static extern bool SetInformationJobObject(WorkerJob job,int info,ref ExtendedLimits limits,uint length);
    [DllImport("kernel32.dll",SetLastError=true)] private static extern bool AssignProcessToJobObject(WorkerJob job,IntPtr process);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    public WorkerJob() : base(true)
    {
        SetHandle(CreateJobObject(IntPtr.Zero,null));
        if(IsInvalid)throw new Win32Exception(Marshal.GetLastWin32Error());
        ExtendedLimits limits=new ExtendedLimits();limits.Basic.Flags=0x2000; // KILL_ON_JOB_CLOSE
        if(!SetInformationJobObject(this,9,ref limits,(uint)Marshal.SizeOf(typeof(ExtendedLimits)))) {
            int error=Marshal.GetLastWin32Error();Dispose();throw new Win32Exception(error);
        }
    }
    public void Assign(Process process)
    {
        if(!AssignProcessToJobObject(this,process.Handle))throw new Win32Exception(Marshal.GetLastWin32Error());
    }
    protected override bool ReleaseHandle(){return CloseHandle(handle);}
}

public sealed class WorkerProcess : IRelayWorker
{
    private readonly Process process;
    private readonly WorkerJob job;
    private readonly object heartbeatLock=new object();
    private readonly object stopLock=new object();
    private DateTime? heartbeat;
    private bool disposed;
    private long sentProbe,receivedProbe;
    private readonly ManualResetEvent stopProbes=new ManualResetEvent(false);
    private Thread probeThread;
    public DateTime StartedUtc {get;private set;}
    public int Id {get {return process.Id;}}
    public bool Alive {get {try{return !disposed&&!process.HasExited;}catch{return false;}}}
    public DateTime? HeartbeatUtc {get {lock(heartbeatLock){return heartbeat;}}}

    public WorkerProcess(string executable,string arguments)
    {
        if(!Path.IsPathRooted(executable))throw new ArgumentException("Worker path must be absolute.");
        job=new WorkerJob();
        process=new Process();
        process.StartInfo=new ProcessStartInfo(executable,arguments) {
            UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,
            RedirectStandardOutput=true,RedirectStandardError=true,WorkingDirectory=Path.GetDirectoryName(executable)
        };
        try {
            if(!process.Start())throw new IOException("无法启动中转进程。");
            StartedUtc=process.StartTime.ToUniversalTime();
            // Worker must wait for GO before touching any configuration or listeners.
            job.Assign(process);
            process.OutputDataReceived+=OnOutput;
            process.ErrorDataReceived+=delegate { }; // Never publish raw worker errors/credentials.
            process.BeginOutputReadLine();process.BeginErrorReadLine();
            process.StandardInput.WriteLine("GO");process.StandardInput.Flush();
            probeThread=new Thread(delegate() {
                while(!stopProbes.WaitOne(0)) {
                    try{Probe();}catch{break;}
                    if(stopProbes.WaitOne(5000))break;
                }
            });
            probeThread.IsBackground=true;probeThread.Name="Relay worker health requests";probeThread.Start();
        } catch {
            try{if(!process.HasExited){process.Kill();process.WaitForExit(3000);}}catch{}
            process.Dispose();job.Dispose();stopProbes.Dispose();throw;
        }
    }
    private void OnOutput(object sender,DataReceivedEventArgs args)
    {
        string line=args.Data;
        if(line==null||!line.StartsWith("PONG ",StringComparison.Ordinal))return;
        long sequence;
        if(!Int64.TryParse(line.Substring(5),out sequence))return;
        lock(heartbeatLock) {
            if(sequence<=receivedProbe||sequence!=sentProbe)return;
            receivedProbe=sequence;heartbeat=DateTime.UtcNow;
        }
    }
    public void Probe()
    {
        lock(stopLock) {
            if(!Alive)return;
            long sequence;
            lock(heartbeatLock) {sequence=++sentProbe;}
            // Caller sends at most one short request per interval. A hung reader is terminated
            // by Stop; the supervisor uses a background probe thread so this cannot block it.
            process.StandardInput.WriteLine("PING "+sequence);process.StandardInput.Flush();
        }
    }
    public void Stop()
    {
        if(disposed)return;
        stopProbes.Set();
        if(Alive) {
            Thread graceful=new Thread(delegate() {
                try {lock(stopLock){process.StandardInput.WriteLine("STOP");process.StandardInput.Flush();}}catch{}
            });
            graceful.IsBackground=true;graceful.Start();
            try{process.WaitForExit(1500);}catch{}
        }
        // Closing our private job is nonblocking and affects only our child, never a PID lookup.
        job.Dispose();
        try{process.WaitForExit(5000);}catch{}
    }
    public void Dispose()
    {
        if(disposed)return;
        Stop();disposed=true;
        bool probesEnded=probeThread==null||probeThread.Join(1000);
        process.Dispose();
        if(probesEnded)stopProbes.Dispose();
    }
}
