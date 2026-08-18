using System;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Rendering;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// Turns TKMS v1 byte payloads into Unity meshes.
    /// </summary>
    /// <remarks>
    /// The work is split so that almost none of it happens on the main thread.
    /// <see cref="Decode"/> is thread-safe and does all the parsing, validation and buffer
    /// preparation; <see cref="Apply"/> touches the Unity Mesh API and must run on the main
    /// thread, where it does nothing but issue buffer copies.
    /// <para>
    /// <b>Coordinate placement.</b> TKMS vertices are local metres in XY. They are written to
    /// the vertex buffer unchanged as (x, y, 0); laying the map flat is the job of a single
    /// rotation on the root transform (see <see cref="TerritoryMapPlacement"/>), not of a
    /// per-vertex transform. Keeping the buffer in the format's own space means the winding
    /// guarantee TKMS makes is the winding the GPU sees.
    /// </para>
    /// </remarks>
    public static class MeshDecoder
    {
        /// <summary>
        /// Decodes and fully validates <paramref name="payload"/>. Safe to call from a worker
        /// thread; does not touch the Unity Mesh API.
        /// </summary>
        /// <param name="payload">Raw TKMS bytes. Not retained — the caller keeps ownership.</param>
        /// <param name="allocator">Allocator for the returned buffers.</param>
        /// <exception cref="TkmsFormatException">The payload is not a TKMS v1 mesh this reader trusts.</exception>
        public static TkmsMeshData Decode(NativeArray<byte> payload,
            Allocator allocator = Allocator.Persistent)
        {
            TkmsHeader header = TkmsHeader.Parse(payload);

            var vertices = default(NativeArray<Vector3>);
            var indices16 = default(NativeArray<ushort>);
            var indices32 = default(NativeArray<uint>);
            var scratch = default(NativeArray<float>);
            bool handedOff = false;

            try
            {
                // One block copy out of the payload, then one pass to expand and validate.
                // Reinterpreting a float array down to bytes is always safe: alignment only
                // ever decreases, never increases.
                scratch = new NativeArray<float>(header.VertexCount * 2, Allocator.Persistent,
                    NativeArrayOptions.UninitializedMemory);
                NativeArray<byte>.Copy(payload, TkmsHeader.SizeInBytes,
                    scratch.Reinterpret<byte>(sizeof(float)), 0, header.VertexCount * 2 * sizeof(float));

                vertices = new NativeArray<Vector3>(header.VertexCount, allocator,
                    NativeArrayOptions.UninitializedMemory);

                float minX = float.PositiveInfinity, minY = float.PositiveInfinity;
                float maxX = float.NegativeInfinity, maxY = float.NegativeInfinity;

                for (int i = 0; i < header.VertexCount; i++)
                {
                    float x = scratch[i * 2];
                    float y = scratch[i * 2 + 1];
                    if (!TkmsHeader.IsFinite(x) || !TkmsHeader.IsFinite(y))
                    {
                        throw new TkmsFormatException(
                            "vertex coordinates contain NaN or infinity (vertex " + i + ")");
                    }

                    vertices[i] = new Vector3(x, y, 0f);

                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                    if (y < minY) minY = y;
                    if (y > maxY) maxY = y;
                }

                // The header's box is checked against the real extent rather than trusted,
                // because a box that lies makes a territory vanish from viewport culling
                // instead of merely looking wrong. Exact comparison matches the reference
                // decoder: both sides are the float32 extremes of the same float32 vertices.
                if (header.MinX != minX || header.MinY != minY ||
                    header.MaxX != maxX || header.MaxY != maxY)
                {
                    throw new TkmsFormatException(
                        "header bbox (" + header.MinX + ", " + header.MinY + ", " + header.MaxX +
                        ", " + header.MaxY + ") does not match the decoded vertices (" + minX +
                        ", " + minY + ", " + maxX + ", " + maxY + "); a bbox that lies makes the " +
                        "region vanish from viewport culling instead of merely looking wrong");
                }

                int indexOffset = (int)header.IndexOffset;
                if (header.UsesUInt32Indices)
                {
                    indices32 = new NativeArray<uint>(header.IndexCount, allocator,
                        NativeArrayOptions.UninitializedMemory);
                    NativeArray<byte>.Copy(payload, indexOffset,
                        indices32.Reinterpret<byte>(sizeof(uint)), 0, header.IndexCount * sizeof(uint));

                    for (int i = 0; i < header.IndexCount; i++)
                    {
                        if (indices32[i] >= (uint)header.VertexCount)
                        {
                            throw new TkmsFormatException(
                                "index " + indices32[i] + " is out of range for " +
                                header.VertexCount + " vertices");
                        }
                    }
                }
                else
                {
                    indices16 = new NativeArray<ushort>(header.IndexCount, allocator,
                        NativeArrayOptions.UninitializedMemory);
                    NativeArray<byte>.Copy(payload, indexOffset,
                        indices16.Reinterpret<byte>(sizeof(ushort)), 0, header.IndexCount * sizeof(ushort));

                    for (int i = 0; i < header.IndexCount; i++)
                    {
                        if (indices16[i] >= header.VertexCount)
                        {
                            throw new TkmsFormatException(
                                "index " + indices16[i] + " is out of range for " +
                                header.VertexCount + " vertices");
                        }
                    }
                }

                var bounds = new Bounds(
                    new Vector3((header.MinX + header.MaxX) * 0.5f,
                                (header.MinY + header.MaxY) * 0.5f, 0f),
                    new Vector3(header.MaxX - header.MinX, header.MaxY - header.MinY, 0f));

                var data = new TkmsMeshData(header, vertices, indices16, indices32, bounds);
                handedOff = true;
                return data;
            }
            finally
            {
                if (scratch.IsCreated)
                {
                    scratch.Dispose();
                }

                if (!handedOff)
                {
                    // A rejected payload must not leak native memory; every early throw above
                    // lands here.
                    if (vertices.IsCreated) vertices.Dispose();
                    if (indices16.IsCreated) indices16.Dispose();
                    if (indices32.IsCreated) indices32.Dispose();
                }
            }
        }

        /// <summary>
        /// Uploads <paramref name="data"/> into <paramref name="mesh"/>. Main thread only.
        /// </summary>
        /// <remarks>
        /// Index validation is skipped here because <see cref="Decode"/> already proved every
        /// index is in range, on a worker thread. Bounds are assigned from the header rather
        /// than recalculated, for the same reason.
        /// </remarks>
        public static void Apply(Mesh mesh, TkmsMeshData data)
        {
            if (mesh == null) throw new ArgumentNullException(nameof(mesh));
            if (data == null) throw new ArgumentNullException(nameof(data));

            const MeshUpdateFlags flags =
                MeshUpdateFlags.DontValidateIndices |
                MeshUpdateFlags.DontRecalculateBounds |
                MeshUpdateFlags.DontNotifyMeshUsers;

            mesh.Clear();
            mesh.indexFormat = data.IndexFormat;

            mesh.SetVertexBufferParams(data.VertexCount,
                new VertexAttributeDescriptor(VertexAttribute.Position, VertexAttributeFormat.Float32, 3));
            mesh.SetVertexBufferData(data.Vertices, 0, 0, data.VertexCount, 0, flags);

            mesh.SetIndexBufferParams(data.IndexCount, data.IndexFormat);
            if (data.Header.UsesUInt32Indices)
            {
                mesh.SetIndexBufferData(data.Indices32, 0, 0, data.IndexCount, flags);
            }
            else
            {
                mesh.SetIndexBufferData(data.Indices16, 0, 0, data.IndexCount, flags);
            }

            mesh.subMeshCount = 1;
            mesh.SetSubMesh(0, new SubMeshDescriptor(0, data.IndexCount, MeshTopology.Triangles)
            {
                firstVertex = 0,
                vertexCount = data.VertexCount,
                bounds = data.Bounds
            }, flags);

            mesh.bounds = data.Bounds;
        }

        /// <summary>
        /// Creates a new mesh from <paramref name="data"/>. Main thread only.
        /// </summary>
        public static Mesh CreateMesh(TkmsMeshData data, string name = null)
        {
            var mesh = new Mesh();
            if (!string.IsNullOrEmpty(name))
            {
                mesh.name = name;
            }

            try
            {
                Apply(mesh, data);
            }
            catch
            {
                UnityEngine.Object.DestroyImmediate(mesh);
                throw;
            }

            return mesh;
        }

        /// <summary>
        /// Decodes and uploads in one call on the calling thread. Convenient for tests and for
        /// small one-off loads; production paths should call <see cref="Decode"/> off the main
        /// thread and <see cref="Apply"/> on it.
        /// </summary>
        public static Mesh DecodeToMesh(NativeArray<byte> payload, string name = null)
        {
            using (var data = Decode(payload))
            {
                return CreateMesh(data, name);
            }
        }
    }
}
