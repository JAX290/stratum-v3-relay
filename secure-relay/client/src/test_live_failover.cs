using System;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading;

public static class LiveFailoverTests
{
    private static int failures;
    private static void Check(bool condition, string name)
    {
        if (condition) Console.WriteLine("PASS " + name);
        else { Console.WriteLine("FAIL " + name); failures++; }
    }

    public static int Main(string[] args)
    {
        if (args.Length != 2) throw new ArgumentException("Expected PFX path and password.");
        X509Certificate2 certificate = new X509Certificate2(args[0], args[1], X509KeyStorageFlags.UserKeySet | X509KeyStorageFlags.Exportable);
        string pin;
        using (SHA256 sha = SHA256.Create()) pin = BitConverter.ToString(sha.ComputeHash(certificate.RawData)).Replace("-", "");
        int primaryPort = FreePort(), backupPort = FreePort(), localPort = FreePort();
        using (FaultTlsServer primary = new FaultTlsServer(primaryPort))
        using (HealthyTlsServer backup = new HealthyTlsServer(backupPort, certificate)) {
            AppConfig config = new AppConfig { ListenAddress="127.0.0.1", SiteName="故障注入测试", HealthCheckMinutes=60 };
            config.Servers.Clear();
            const string key = "0123456789abcdef0123456789abcdef";
            config.Servers.Add(new ServerProfile { Name="主VPS", Enabled=true, Address="127.0.0.1", Port=primaryPort, ServerName="localhost", CertificateSha256=pin, SharedKey=key });
            config.Servers.Add(new ServerProfile { Name="备用VPS 1", Enabled=true, Address="127.0.0.1", Port=backupPort, ServerName="localhost", CertificateSha256=pin, SharedKey=key });
            config.Servers.Add(new ServerProfile { Name="备用VPS 2", Enabled=false });
            RelayManager relay = new RelayManager(delegate(string message) { Console.WriteLine("LOG " + message); });
            relay.Start(config, new[] { new PortRoute { LocalPort=localPort, RemotePort=3333 } });
            try {
                Attempt(localPort, false);
                Thread.Sleep(5200);
                Attempt(localPort, false);
                Thread.Sleep(5200);
                Attempt(localPort, false);
                Thread.Sleep(300);
                Attempt(localPort, true);
                DateTime deadline = DateTime.UtcNow.AddSeconds(5);
                RelaySnapshot snapshot;
                do { snapshot=relay.Snapshot(); if(Selected(snapshot,"备用VPS 1"))break; Thread.Sleep(50); } while(DateTime.UtcNow<deadline);
                Check(primary.Accepted >= 3, "real TLS handshake failures reach the primary threshold");
                Check(backup.Authenticated >= 1, "backup completes a real TLS handshake and relay authentication");
                Check(Selected(snapshot,"备用VPS 1"), "new relay connections select backup after primary cooldown");
                Check(snapshot.Failures >= 2, "failed miner setup attempts remain observable");
            } finally { relay.Stop(); }
        }
        return failures == 0 ? 0 : 1;
    }

    private static bool Selected(RelaySnapshot snapshot, string name)
    {
        foreach (EndpointState state in snapshot.Endpoints)
            if (String.Equals(state.Name,name,StringComparison.OrdinalIgnoreCase) && state.Selected) return true;
        return false;
    }

    private static void Attempt(int port, bool expectReply)
    {
        using (TcpClient miner = new TcpClient()) {
            miner.ReceiveTimeout=4000; miner.SendTimeout=4000; miner.Connect(IPAddress.Loopback,port);
            NetworkStream stream=miner.GetStream(); stream.WriteByte(42);
            if (expectReply) Check(stream.ReadByte()==43,"miner traffic crosses the selected backup");
            else { try { stream.ReadByte(); } catch (IOException) { } }
        }
        Thread.Sleep(250);
    }

    private static int FreePort()
    {
        TcpListener listener=new TcpListener(IPAddress.Loopback,0); listener.Start();
        int port=((IPEndPoint)listener.LocalEndpoint).Port; listener.Stop(); return port;
    }

    private sealed class FaultTlsServer : IDisposable
    {
        private readonly TcpListener listener; private readonly Thread thread; private volatile bool stopping; private int accepted;
        public int Accepted { get { return Volatile.Read(ref accepted); } }
        public FaultTlsServer(int port) { listener=new TcpListener(IPAddress.Loopback,port);listener.Start();thread=new Thread(Run){IsBackground=true};thread.Start(); }
        private void Run() { while(!stopping)try{TcpClient client=listener.AcceptTcpClient();Interlocked.Increment(ref accepted);client.Close();}catch(SocketException){if(!stopping)throw;} }
        public void Dispose(){stopping=true;listener.Stop();thread.Join(2000);}
    }

    private sealed class HealthyTlsServer : IDisposable
    {
        private readonly TcpListener listener; private readonly Thread thread; private readonly X509Certificate2 certificate; private volatile bool stopping; private int authenticated;
        public int Authenticated { get { return Volatile.Read(ref authenticated); } }
        public HealthyTlsServer(int port,X509Certificate2 cert){certificate=cert;listener=new TcpListener(IPAddress.Loopback,port);listener.Start();thread=new Thread(Run){IsBackground=true};thread.Start();}
        private void Run(){while(!stopping)try{TcpClient client=listener.AcceptTcpClient();ThreadPool.QueueUserWorkItem(delegate{Serve(client);});}catch(SocketException){if(!stopping)throw;}}
        private void Serve(TcpClient client){using(client)using(SslStream tls=new SslStream(client.GetStream(),false)){try{tls.AuthenticateAsServer(certificate,false,SslProtocols.Tls12,false);Interlocked.Increment(ref authenticated);ReadHeader(tls);byte[] ok=Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n");tls.Write(ok,0,ok.Length);int value=tls.ReadByte();if(value>=0)tls.WriteByte(43);}catch(Exception ex){Console.WriteLine("TLS SERVER "+ex.GetType().Name+": "+ex.Message);}}}
        private static void ReadHeader(Stream stream){int matched=0;while(matched<4){int value=stream.ReadByte();if(value<0)throw new EndOfStreamException();byte expected=(byte)(matched==0||matched==2?'\r':'\n');matched=value==expected?matched+1:(value=='\r'?1:0);}}
        public void Dispose(){stopping=true;listener.Stop();thread.Join(2000);}
    }
}
