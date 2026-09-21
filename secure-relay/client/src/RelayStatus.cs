using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
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
using System.Web.Script.Serialization;
using Microsoft.Win32;

[DataContract] public sealed class EndpointState { [DataMember]public string Name=""; [DataMember]public bool Online; [DataMember]public int LatencyMs; [DataMember]public long Failures; [DataMember]public string LastError=""; [DataMember]public DateTime LastCheck; public EndpointState Copy(){return (EndpointState)MemberwiseClone();} }
[DataContract] public sealed class RelaySnapshot { [DataMember]public bool Running; [DataMember]public int Active,ActiveMiners; [DataMember]public long Total,Failures,Uploaded,Downloaded; [DataMember]public DateTime StartedAt; [DataMember]public List<EndpointState> Endpoints=new List<EndpointState>(); }
