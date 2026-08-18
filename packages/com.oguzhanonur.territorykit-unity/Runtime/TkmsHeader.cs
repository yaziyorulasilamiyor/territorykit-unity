using System;
using Unity.Collections;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// The parsed 32-byte TKMS v1 header. See <c>docs/mesh-format.md</c> for the specification.
    /// </summary>
    /// <remarks>
    /// Parsing is pure and allocation-free, so it runs on a worker thread. Only the checks that
    /// need the body (finite coordinates, index range, the truthfulness of the bounding box) are
    /// left to <see cref="MeshDecoder"/>.
    /// </remarks>
    public readonly struct TkmsHeader
    {
        /// <summary>Header length in bytes.</summary>
        public const int SizeInBytes = 32;

        /// <summary>Highest vertex count a 16-bit index buffer can address.</summary>
        public const int MaxUInt16VertexCount = 65535;

        /// <summary>Bit 0 of <see cref="Flags"/>: indices are uint32 rather than uint16.</summary>
        public const int FlagUInt32Indices = 1;

        private const int SupportedVersion = 1;

        public int Version { get; }

        /// <summary>
        /// Raw flag word. Only bit 0 is defined in v1; unknown bits are ignored rather than
        /// rejected, so v1-compatible extensions stay readable (docs/mesh-format.md).
        /// </summary>
        public int Flags { get; }

        public int VertexCount { get; }

        public int IndexCount { get; }

        public float MinX { get; }

        public float MinY { get; }

        public float MaxX { get; }

        public float MaxY { get; }

        /// <summary>
        /// True when the index buffer is uint32. Read from the payload, never inferred from the
        /// vertex count: the encoder derives this bit so a writer cannot forget it, and a reader
        /// that re-derived it would disagree with the bytes it was handed.
        /// </summary>
        public bool UsesUInt32Indices => (Flags & FlagUInt32Indices) != 0;

        public int IndexElementSize => UsesUInt32Indices ? 4 : 2;

        public int TriangleCount => IndexCount / 3;

        /// <summary>Byte offset at which the index section starts.</summary>
        public long IndexOffset => SizeInBytes + (long)VertexCount * 2 * sizeof(float);

        /// <summary>
        /// Total bytes this header declares. A payload shorter than this is invalid; extra
        /// trailing bytes are ignored (docs/mesh-format.md — that tolerance is for readers only).
        /// </summary>
        public long PayloadLength => IndexOffset + (long)IndexCount * IndexElementSize;

        private TkmsHeader(int version, int flags, int vertexCount, int indexCount,
            float minX, float minY, float maxX, float maxY)
        {
            Version = version;
            Flags = flags;
            VertexCount = vertexCount;
            IndexCount = indexCount;
            MinX = minX;
            MinY = minY;
            MaxX = maxX;
            MaxY = maxY;
        }

        /// <summary>
        /// Parses and validates the header of <paramref name="payload"/>. Safe to call from any
        /// thread.
        /// </summary>
        /// <exception cref="TkmsFormatException">The payload is not a readable TKMS v1 mesh.</exception>
        public static TkmsHeader Parse(NativeArray<byte> payload)
        {
            if (!BitConverter.IsLittleEndian)
            {
                throw new TkmsFormatException(
                    "TKMS is a little-endian format and this reader decodes in host order; " +
                    "big-endian hosts are not supported");
            }

            int length = payload.IsCreated ? payload.Length : 0;
            if (length < SizeInBytes)
            {
                throw new TkmsFormatException(
                    "payload is " + length + " bytes, shorter than the " + SizeInBytes +
                    "-byte header");
            }

            if (payload[0] != (byte)'T' || payload[1] != (byte)'K' ||
                payload[2] != (byte)'M' || payload[3] != (byte)'S')
            {
                throw new TkmsFormatException(
                    "bad magic \"" + Printable(payload, 0, 4) + "\", expected \"TKMS\"");
            }

            int version = ReadUInt16(payload, 4);
            if (version != SupportedVersion)
            {
                throw new TkmsFormatException(
                    "unsupported TKMS version " + version + ", this reader implements v1");
            }

            int flags = ReadUInt16(payload, 6);
            uint vertexCount = ReadUInt32(payload, 8);
            uint indexCount = ReadUInt32(payload, 12);

            // Guard before either count is used in arithmetic. The format allows values that do
            // not fit a signed int, and a silently wrapped count would turn a bad payload into a
            // plausible-looking one.
            if (vertexCount > int.MaxValue || indexCount > int.MaxValue)
            {
                throw new TkmsFormatException(
                    "declared counts are out of range (vertexCount " + vertexCount +
                    ", indexCount " + indexCount + ")");
            }

            bool usesUInt32 = (flags & FlagUInt32Indices) != 0;
            if (vertexCount > MaxUInt16VertexCount && !usesUInt32)
            {
                throw new TkmsFormatException(
                    "vertexCount " + vertexCount + " exceeds " + MaxUInt16VertexCount +
                    " but the uint32 index flag is not set");
            }

            if (vertexCount == 0 || indexCount == 0)
            {
                throw new TkmsFormatException(
                    "empty geometry: a mesh must carry at least one triangle");
            }

            if (indexCount % 3 != 0)
            {
                throw new TkmsFormatException(
                    "indexCount " + indexCount + " is not a multiple of 3");
            }

            var header = new TkmsHeader(
                version, flags, (int)vertexCount, (int)indexCount,
                ReadSingle(payload, 16), ReadSingle(payload, 20),
                ReadSingle(payload, 24), ReadSingle(payload, 28));

            if (length < header.PayloadLength)
            {
                throw new TkmsFormatException(
                    "payload is " + length + " bytes, the header declares " + header.PayloadLength);
            }

            // The bbox is checked for shape here and for truthfulness in MeshDecoder, which is
            // the first place the real vertex extent is known.
            if (!IsFinite(header.MinX) || !IsFinite(header.MinY) ||
                !IsFinite(header.MaxX) || !IsFinite(header.MaxY))
            {
                throw new TkmsFormatException(
                    "header bbox contains NaN or infinity: (" + header.MinX + ", " + header.MinY +
                    ", " + header.MaxX + ", " + header.MaxY + ")");
            }

            if (header.MinX > header.MaxX || header.MinY > header.MaxY)
            {
                throw new TkmsFormatException(
                    "header bbox is inverted: min (" + header.MinX + ", " + header.MinY +
                    ") is past max (" + header.MaxX + ", " + header.MaxY + ")");
            }

            return header;
        }

        internal static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }

        private static int ReadUInt16(NativeArray<byte> data, int offset)
        {
            return data[offset] | (data[offset + 1] << 8);
        }

        private static uint ReadUInt32(NativeArray<byte> data, int offset)
        {
            return (uint)data[offset]
                 | ((uint)data[offset + 1] << 8)
                 | ((uint)data[offset + 2] << 16)
                 | ((uint)data[offset + 3] << 24);
        }

        private static float ReadSingle(NativeArray<byte> data, int offset)
        {
            return BitConverter.Int32BitsToSingle((int)ReadUInt32(data, offset));
        }

        private static string Printable(NativeArray<byte> data, int offset, int count)
        {
            var chars = new char[count];
            for (int i = 0; i < count; i++)
            {
                byte b = data[offset + i];
                chars[i] = b >= 0x20 && b < 0x7f ? (char)b : '?';
            }

            return new string(chars);
        }
    }
}
