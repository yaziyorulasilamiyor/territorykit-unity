using System;
using System.Collections.Generic;
using System.Text;
using Unity.Collections;

namespace TerritoryKit.Unity
{
    /// <summary>One entry of a decoded TKMB container: a territory id and the slice that holds its mesh.</summary>
    public readonly struct TkmbEntry
    {
        public TkmbEntry(string territoryId, int offset, int length)
        {
            TerritoryId = territoryId;
            Offset = offset;
            Length = length;
        }

        public string TerritoryId { get; }

        /// <summary>Offset into the container payload, absolute from the start of the buffer.</summary>
        public int Offset { get; }

        public int Length { get; }
    }

    /// <summary>
    /// Reads the TKMB v1 batch container returned by
    /// <c>POST /v1/datasets/{id}/revisions/{rev}/mesh/batch</c>. See <c>docs/mesh-format.md</c>.
    /// </summary>
    /// <remarks>
    /// Parsing is pure and thread-safe. Entries are returned as offsets into the caller's buffer
    /// rather than copies, so a batch of 81 meshes costs one allocation for the download and
    /// nothing more until each mesh is decoded.
    /// </remarks>
    public static class TkmbContainer
    {
        public const int HeaderSizeInBytes = 16;

        private const int FlagGzipEntries = 1;

        /// <summary>Result of reading a container's table of contents.</summary>
        public sealed class Toc
        {
            internal Toc(List<TkmbEntry> entries, List<string> missing)
            {
                Entries = entries;
                Missing = missing;
            }

            /// <summary>Found meshes, in ascending territory id order as the format requires.</summary>
            public List<TkmbEntry> Entries { get; }

            /// <summary>
            /// Ids that were requested but not found. These travel inside the container rather
            /// than in a header, so an intermediary that drops headers cannot lose them.
            /// </summary>
            public List<string> Missing { get; }
        }

        /// <summary>Parses the header and table of contents of <paramref name="payload"/>.</summary>
        /// <exception cref="TkmsFormatException">The payload is not a readable TKMB v1 container.</exception>
        public static Toc ReadToc(NativeArray<byte> payload)
        {
            int length = payload.IsCreated ? payload.Length : 0;
            if (length < HeaderSizeInBytes)
            {
                throw new TkmsFormatException(
                    "payload is " + length + " bytes, shorter than the 16-byte TKMB header");
            }

            if (payload[0] != (byte)'T' || payload[1] != (byte)'K' ||
                payload[2] != (byte)'M' || payload[3] != (byte)'B')
            {
                throw new TkmsFormatException("bad magic, expected \"TKMB\"");
            }

            int version = ReadUInt16(payload, 4);
            if (version != 1)
            {
                throw new TkmsFormatException(
                    "unsupported TKMB version " + version + ", this reader implements v1");
            }

            int flags = ReadUInt16(payload, 6);
            if ((flags & FlagGzipEntries) != 0)
            {
                // The client asks for identity entries, so reaching this means the server
                // answered a question it was not asked. Per-entry gzip is a Phase 5 concern,
                // where bandwidth starts to matter.
                throw new TkmsFormatException(
                    "container holds gzip-compressed entries; this client requests " +
                    "entryEncoding 'identity' and cannot read them");
            }

            long foundCount = ReadUInt32(payload, 8);
            long missingCount = ReadUInt32(payload, 12);
            if (foundCount > int.MaxValue || missingCount > int.MaxValue)
            {
                throw new TkmsFormatException(
                    "declared counts are out of range (found " + foundCount +
                    ", missing " + missingCount + ")");
            }

            var entries = new List<TkmbEntry>((int)foundCount);
            var missing = new List<string>((int)missingCount);

            int cursor = HeaderSizeInBytes;
            var rawEntries = new List<TkmbEntry>((int)foundCount);
            for (long i = 0; i < foundCount; i++)
            {
                string id = ReadId(payload, ref cursor);
                long offset = ReadUInt32(payload, Require(payload, ref cursor, 4));
                long entryLength = ReadUInt32(payload, Require(payload, ref cursor, 4));
                if (offset > int.MaxValue || entryLength > int.MaxValue)
                {
                    throw new TkmsFormatException(
                        "entry '" + id + "' declares an out-of-range slice");
                }

                rawEntries.Add(new TkmbEntry(id, (int)offset, (int)entryLength));
            }

            for (long i = 0; i < missingCount; i++)
            {
                missing.Add(ReadId(payload, ref cursor));
            }

            // Offsets are relative to the start of the payload section, which begins right after
            // the table of contents; rebasing them here means callers never have to know that.
            int payloadStart = cursor;
            foreach (TkmbEntry entry in rawEntries)
            {
                long absolute = (long)payloadStart + entry.Offset;
                if (absolute + entry.Length > length)
                {
                    throw new TkmsFormatException(
                        "entry '" + entry.TerritoryId + "' runs past the end of the container");
                }

                entries.Add(new TkmbEntry(entry.TerritoryId, (int)absolute, entry.Length));
            }

            return new Toc(entries, missing);
        }

        private static string ReadId(NativeArray<byte> payload, ref int cursor)
        {
            int lengthOffset = Require(payload, ref cursor, 2);
            int idLength = ReadUInt16(payload, lengthOffset);
            int idOffset = Require(payload, ref cursor, idLength);

            var bytes = new byte[idLength];
            for (int i = 0; i < idLength; i++)
            {
                bytes[i] = payload[idOffset + i];
            }

            return Encoding.UTF8.GetString(bytes);
        }

        private static int Require(NativeArray<byte> payload, ref int cursor, int count)
        {
            if (count < 0 || cursor + count > payload.Length)
            {
                throw new TkmsFormatException(
                    "container is truncated: needed " + count + " bytes at offset " + cursor +
                    " of " + payload.Length);
            }

            int offset = cursor;
            cursor += count;
            return offset;
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
    }
}
