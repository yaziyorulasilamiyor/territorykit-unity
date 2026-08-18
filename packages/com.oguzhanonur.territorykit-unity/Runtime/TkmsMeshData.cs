using System;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Rendering;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// A fully decoded and validated TKMS mesh, held in native memory and ready to be uploaded.
    /// </summary>
    /// <remarks>
    /// This is the hand-off between the worker thread that decodes and the main thread that
    /// uploads. Everything expensive has already happened by the time one of these exists;
    /// <see cref="MeshDecoder.Apply"/> only issues buffer copies.
    /// <para>
    /// The caller owns the instance and must <see cref="Dispose"/> it. Disposing after
    /// <see cref="MeshDecoder.Apply"/> is correct — the mesh keeps its own copy of the data.
    /// </para>
    /// </remarks>
    public sealed class TkmsMeshData : IDisposable
    {
        private NativeArray<Vector3> _vertices;
        private NativeArray<ushort> _indices16;
        private NativeArray<uint> _indices32;
        private bool _disposed;

        internal TkmsMeshData(TkmsHeader header, NativeArray<Vector3> vertices,
            NativeArray<ushort> indices16, NativeArray<uint> indices32, Bounds bounds)
        {
            Header = header;
            _vertices = vertices;
            _indices16 = indices16;
            _indices32 = indices32;
            Bounds = bounds;
        }

        public TkmsHeader Header { get; }

        /// <summary>
        /// Local bounds taken from the header, not recomputed. The header's box is required to
        /// be the true extent of the vertices and the decoder has already verified that, so
        /// recomputing here would only cost time to reach the same numbers.
        /// </summary>
        public Bounds Bounds { get; }

        public int VertexCount => Header.VertexCount;

        public int IndexCount => Header.IndexCount;

        public IndexFormat IndexFormat =>
            Header.UsesUInt32Indices ? IndexFormat.UInt32 : IndexFormat.UInt16;

        /// <summary>Positions in mesh-local XY, with Z left at zero. See <see cref="MeshDecoder"/>.</summary>
        public NativeArray<Vector3> Vertices => _vertices;

        public NativeArray<ushort> Indices16 => _indices16;

        public NativeArray<uint> Indices32 => _indices32;

        public void Dispose()
        {
            if (_disposed)
            {
                return;
            }

            _disposed = true;
            if (_vertices.IsCreated) _vertices.Dispose();
            if (_indices16.IsCreated) _indices16.Dispose();
            if (_indices32.IsCreated) _indices32.Dispose();
        }
    }
}
