using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// Builds TKMB v1 batch containers by hand, the counterpart to <see cref="TkmsFixture"/>.
    /// </summary>
    public static class TkmbFixture
    {
        /// <summary>
        /// Packs <paramref name="entries"/> into a container. Ids are sorted ascending, which
        /// the format requires regardless of the order they were requested in.
        /// </summary>
        public static byte[] Build(IDictionary<string, byte[]> entries, IEnumerable<string> missing = null)
        {
            var ids = new List<string>(entries.Keys);
            ids.Sort(StringComparer.Ordinal);

            var missingIds = new List<string>();
            if (missing != null)
            {
                missingIds.AddRange(missing);
            }

            missingIds.Sort(StringComparer.Ordinal);

            using (var stream = new MemoryStream())
            using (var writer = new BinaryWriter(stream))
            {
                writer.Write((byte)'T');
                writer.Write((byte)'K');
                writer.Write((byte)'M');
                writer.Write((byte)'B');
                writer.Write((ushort)1);
                writer.Write((ushort)0); // identity entries
                writer.Write((uint)ids.Count);
                writer.Write((uint)missingIds.Count);

                uint offset = 0;
                foreach (string id in ids)
                {
                    byte[] idBytes = Encoding.UTF8.GetBytes(id);
                    writer.Write((ushort)idBytes.Length);
                    writer.Write(idBytes);
                    writer.Write(offset);
                    writer.Write((uint)entries[id].Length);
                    offset += (uint)entries[id].Length;
                }

                foreach (string id in missingIds)
                {
                    byte[] idBytes = Encoding.UTF8.GetBytes(id);
                    writer.Write((ushort)idBytes.Length);
                    writer.Write(idBytes);
                }

                foreach (string id in ids)
                {
                    writer.Write(entries[id]);
                }

                writer.Flush();
                return stream.ToArray();
            }
        }
    }
}
