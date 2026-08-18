using System;
using System.IO;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// Builds TKMS v1 byte payloads by hand, so decoder tests compare against bytes a human
    /// wrote rather than against whatever the decoder happens to produce.
    /// </summary>
    /// <remarks>
    /// <see cref="BinaryWriter"/> is little-endian on every .NET runtime Unity ships, which is
    /// the byte order TKMS specifies.
    /// </remarks>
    public static class TkmsFixture
    {
        /// <summary>A single clockwise triangle: (0,0) -> (0,10) -> (10,0), signed area -50.</summary>
        public static readonly float[] TriangleVertices = { 0f, 0f, 0f, 10f, 10f, 0f };

        public static readonly int[] TriangleIndices = { 0, 1, 2 };

        /// <summary>
        /// Two clockwise triangles covering a 100x100 square, the shape the render tests project.
        /// </summary>
        public static readonly float[] QuadVertices = { 0f, 0f, 0f, 100f, 100f, 100f, 100f, 0f };

        public static readonly int[] QuadIndices = { 0, 1, 2, 0, 2, 3 };

        public static byte[] Triangle()
        {
            return Build(TriangleVertices, TriangleIndices);
        }

        public static byte[] Quad()
        {
            return Build(QuadVertices, QuadIndices);
        }

        /// <summary>
        /// Options that let a test bend exactly one rule at a time. Everything left unset is
        /// derived so the payload stays otherwise valid.
        /// </summary>
        public sealed class Options
        {
            /// <summary>Write uint32 indices even when the vertex count does not require it.</summary>
            public bool ForceUInt32Indices;

            /// <summary>Write uint16 indices even when the vertex count requires uint32.</summary>
            public bool ForceUInt16Indices;

            /// <summary>Extra bits OR-ed into the flag word (v1 leaves bits 1..15 undefined).</summary>
            public int ExtraFlags;

            /// <summary>Bytes appended past the declared payload length.</summary>
            public int TrailingBytes;

            /// <summary>Bytes removed from the end of the declared payload.</summary>
            public int TruncateBytes;

            /// <summary>Replaces the four-float bbox instead of deriving it from the vertices.</summary>
            public float[] BboxOverride;

            public string Magic = "TKMS";

            public int Version = 1;

            /// <summary>Overrides the declared vertexCount without changing the body.</summary>
            public long? VertexCountOverride;

            /// <summary>Overrides the declared indexCount without changing the body.</summary>
            public long? IndexCountOverride;
        }

        public static byte[] Build(float[] verticesXY, int[] indices)
        {
            return Build(verticesXY, indices, new Options());
        }

        public static byte[] Build(float[] verticesXY, int[] indices, Options options)
        {
            if (verticesXY == null) throw new ArgumentNullException(nameof(verticesXY));
            if (indices == null) throw new ArgumentNullException(nameof(indices));
            if (options == null) throw new ArgumentNullException(nameof(options));
            if (verticesXY.Length % 2 != 0)
            {
                throw new ArgumentException("vertex array must hold XY pairs", nameof(verticesXY));
            }

            int vertexCount = verticesXY.Length / 2;
            bool uint32 = options.ForceUInt32Indices ||
                          (vertexCount > TkmsHeader.MaxUInt16VertexCount &&
                           !options.ForceUInt16Indices);

            int flags = (uint32 ? TkmsHeader.FlagUInt32Indices : 0) | options.ExtraFlags;
            float[] bbox = options.BboxOverride ?? DeriveBbox(verticesXY);

            using (var stream = new MemoryStream())
            using (var writer = new BinaryWriter(stream))
            {
                foreach (char c in options.Magic)
                {
                    writer.Write((byte)c);
                }

                writer.Write((ushort)options.Version);
                writer.Write((ushort)flags);
                writer.Write((uint)(options.VertexCountOverride ?? vertexCount));
                writer.Write((uint)(options.IndexCountOverride ?? indices.Length));
                writer.Write(bbox[0]);
                writer.Write(bbox[1]);
                writer.Write(bbox[2]);
                writer.Write(bbox[3]);

                foreach (float value in verticesXY)
                {
                    writer.Write(value);
                }

                foreach (int index in indices)
                {
                    if (uint32)
                    {
                        writer.Write((uint)index);
                    }
                    else
                    {
                        writer.Write((ushort)index);
                    }
                }

                writer.Flush();
                byte[] payload = stream.ToArray();

                if (options.TruncateBytes > 0)
                {
                    Array.Resize(ref payload, Math.Max(0, payload.Length - options.TruncateBytes));
                }

                if (options.TrailingBytes > 0)
                {
                    int start = payload.Length;
                    Array.Resize(ref payload, payload.Length + options.TrailingBytes);
                    for (int i = start; i < payload.Length; i++)
                    {
                        payload[i] = 0xAB;
                    }
                }

                return payload;
            }
        }

        /// <summary>
        /// A fan of <paramref name="triangleCount"/> clockwise triangles with more than 65535
        /// vertices, so the uint32 index path has something to decode. The real Turkish dataset
        /// never reaches this size (240,379 vertices spread over 81 territories), so without a
        /// synthetic fixture that code path would ship untested.
        /// </summary>
        public static byte[] LargeUInt32Mesh(int triangleCount)
        {
            int vertexCount = triangleCount * 3;
            if (vertexCount <= TkmsHeader.MaxUInt16VertexCount)
            {
                throw new ArgumentException(
                    "fixture must exceed " + TkmsHeader.MaxUInt16VertexCount + " vertices to " +
                    "exercise uint32 indices", nameof(triangleCount));
            }

            var vertices = new float[vertexCount * 2];
            var indices = new int[triangleCount * 3];
            for (int t = 0; t < triangleCount; t++)
            {
                float x = t;
                int v = t * 3;
                // Clockwise with +X right and +Y up: (x,0) -> (x,1) -> (x+0.5,0).
                vertices[v * 2 + 0] = x;
                vertices[v * 2 + 1] = 0f;
                vertices[v * 2 + 2] = x;
                vertices[v * 2 + 3] = 1f;
                vertices[v * 2 + 4] = x + 0.5f;
                vertices[v * 2 + 5] = 0f;

                indices[v + 0] = v;
                indices[v + 1] = v + 1;
                indices[v + 2] = v + 2;
            }

            return Build(vertices, indices);
        }

        public static float[] DeriveBbox(float[] verticesXY)
        {
            float minX = float.PositiveInfinity, minY = float.PositiveInfinity;
            float maxX = float.NegativeInfinity, maxY = float.NegativeInfinity;
            for (int i = 0; i < verticesXY.Length; i += 2)
            {
                minX = Math.Min(minX, verticesXY[i]);
                maxX = Math.Max(maxX, verticesXY[i]);
                minY = Math.Min(minY, verticesXY[i + 1]);
                maxY = Math.Max(maxY, verticesXY[i + 1]);
            }

            return new[] { minX, minY, maxX, maxY };
        }
    }
}
