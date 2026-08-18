using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// A tiny in-process geometry API, enough for the client to talk to without a Python server.
    /// </summary>
    /// <remarks>
    /// Built on <see cref="HttpListener"/>, which needs no elevation for a <c>localhost</c>
    /// prefix on Windows and no third-party dependency anywhere. Responses are canned rather
    /// than computed: the point is to exercise the client's transport, JSON and container
    /// handling, not to re-test the server.
    /// </remarks>
    public sealed class MockGeometryServer : IDisposable
    {
        private readonly HttpListener _listener = new HttpListener();
        private readonly Thread _thread;
        private volatile bool _running = true;

        public MockGeometryServer()
        {
            Port = FindFreePort();
            BaseUrl = "http://localhost:" + Port.ToString(CultureInfo.InvariantCulture);
            _listener.Prefixes.Add(BaseUrl + "/");
            _listener.Start();

            _thread = new Thread(Serve) { IsBackground = true, Name = "MockGeometryServer" };
            _thread.Start();
        }

        public int Port { get; }

        public string BaseUrl { get; }

        /// <summary>Requests seen so far, as "METHOD path?query". Read after a call to assert on it.</summary>
        public List<string> Requests { get; } = new List<string>();

        /// <summary>JSON returned by <c>GET /v1/datasets/{id}</c>.</summary>
        public string DatasetJson { get; set; }

        /// <summary>JSON returned by <c>GET /v1/datasets/{id}/territories</c>, in page order.</summary>
        public List<string> TerritoryPages { get; } = new List<string>();

        /// <summary>
        /// JSON returned by <c>GET /v1/datasets/{id}/viewport</c>, in page order. Served
        /// regardless of the requested <c>bbox</c> — this is a transport fake, not a spatial one;
        /// callers that need "only these ids are in view" drive it by queuing the page a real
        /// server's spatial filter would have produced for that camera position.
        /// </summary>
        public List<string> ViewportPages { get; } = new List<string>();

        /// <summary>TKMS payload per territory id, served by the single-mesh endpoint.</summary>
        public Dictionary<string, byte[]> Meshes { get; } = new Dictionary<string, byte[]>();

        /// <summary>Ids the batch endpoint reports as missing.</summary>
        public List<string> MissingIds { get; } = new List<string>();

        /// <summary>Status code and body to answer the next matching request with, if set.</summary>
        public int ForcedStatus { get; set; }

        public string ForcedBody { get; set; }

        /// <summary>Seconds to stall every response by, for cancellation tests.</summary>
        public double DelaySeconds { get; set; }

        private static int FindFreePort()
        {
            var probe = new TcpListener(IPAddress.Loopback, 0);
            probe.Start();
            int port = ((IPEndPoint)probe.LocalEndpoint).Port;
            probe.Stop();
            return port;
        }

        private void Serve()
        {
            while (_running)
            {
                HttpListenerContext context;
                try
                {
                    context = _listener.GetContext();
                }
                catch (Exception)
                {
                    return; // listener stopped
                }

                try
                {
                    Handle(context);
                }
                catch (Exception)
                {
                    try
                    {
                        context.Response.StatusCode = 500;
                        context.Response.Close();
                    }
                    catch (Exception)
                    {
                        // The client may already be gone; nothing useful to do here.
                    }
                }
            }
        }

        private void Handle(HttpListenerContext context)
        {
            string path = context.Request.Url.AbsolutePath;
            lock (Requests)
            {
                Requests.Add(context.Request.HttpMethod + " " + path +
                             (string.IsNullOrEmpty(context.Request.Url.Query)
                                 ? string.Empty
                                 : context.Request.Url.Query));
            }

            if (DelaySeconds > 0)
            {
                Thread.Sleep(TimeSpan.FromSeconds(DelaySeconds));
            }

            if (ForcedStatus != 0)
            {
                WriteJson(context, ForcedStatus, ForcedBody ?? "{}");
                return;
            }

            if (path.EndsWith("/mesh/batch", StringComparison.Ordinal))
            {
                HandleBatch(context);
                return;
            }

            int meshIndex = path.IndexOf("/mesh/", StringComparison.Ordinal);
            if (meshIndex >= 0)
            {
                string territoryId = Uri.UnescapeDataString(path.Substring(meshIndex + "/mesh/".Length));
                if (Meshes.TryGetValue(territoryId, out byte[] payload))
                {
                    WriteBytes(context, 200, payload);
                }
                else
                {
                    WriteJson(context, 404,
                        "{\"error\":{\"code\":\"territory_not_found\",\"message\":\"no such territory\"}}");
                }

                return;
            }

            if (path.EndsWith("/territories", StringComparison.Ordinal))
            {
                int page = 0;
                string cursor = context.Request.QueryString["cursor"];
                if (!string.IsNullOrEmpty(cursor))
                {
                    int.TryParse(cursor, NumberStyles.Integer, CultureInfo.InvariantCulture, out page);
                }

                WriteJson(context, 200,
                    page < TerritoryPages.Count ? TerritoryPages[page] : "{\"items\":[]}");
                return;
            }

            if (path.EndsWith("/viewport", StringComparison.Ordinal))
            {
                int page = 0;
                string cursor = context.Request.QueryString["cursor"];
                if (!string.IsNullOrEmpty(cursor))
                {
                    int.TryParse(cursor, NumberStyles.Integer, CultureInfo.InvariantCulture, out page);
                }

                WriteJson(context, 200,
                    page < ViewportPages.Count ? ViewportPages[page] : "{\"territoryIds\":[]}");
                return;
            }

            WriteJson(context, 200, DatasetJson ?? "{}");
        }

        private void HandleBatch(HttpListenerContext context)
        {
            string body;
            using (var reader = new StreamReader(context.Request.InputStream, Encoding.UTF8))
            {
                body = reader.ReadToEnd();
            }

            // Deliberately crude: the ids are pulled out of the JSON without a parser, because
            // the server's job here is to prove the client sent them, not to validate JSON.
            var requested = new List<string>();
            int start = body.IndexOf('[');
            int end = body.IndexOf(']', start + 1);
            if (start >= 0 && end > start)
            {
                foreach (string raw in body.Substring(start + 1, end - start - 1).Split(','))
                {
                    string id = raw.Trim().Trim('"');
                    if (id.Length > 0)
                    {
                        requested.Add(id);
                    }
                }
            }

            var found = new Dictionary<string, byte[]>();
            var missing = new List<string>();
            foreach (string id in requested)
            {
                if (Meshes.TryGetValue(id, out byte[] payload) && !MissingIds.Contains(id))
                {
                    found[id] = payload;
                }
                else
                {
                    missing.Add(id);
                }
            }

            WriteBytes(context, 200, TkmbFixture.Build(found, missing));
        }

        private static void WriteJson(HttpListenerContext context, int status, string json)
        {
            byte[] bytes = Encoding.UTF8.GetBytes(json);
            context.Response.StatusCode = status;
            context.Response.ContentType = "application/json";
            context.Response.ContentLength64 = bytes.Length;
            context.Response.OutputStream.Write(bytes, 0, bytes.Length);
            context.Response.Close();
        }

        private static void WriteBytes(HttpListenerContext context, int status, byte[] bytes)
        {
            context.Response.StatusCode = status;
            context.Response.ContentType = "application/octet-stream";
            context.Response.ContentLength64 = bytes.Length;
            context.Response.OutputStream.Write(bytes, 0, bytes.Length);
            context.Response.Close();
        }

        public void Dispose()
        {
            _running = false;
            try
            {
                _listener.Stop();
                _listener.Close();
            }
            catch (Exception)
            {
                // Already torn down.
            }

            if (_thread != null && _thread.IsAlive)
            {
                _thread.Join(TimeSpan.FromSeconds(2));
            }
        }
    }
}
