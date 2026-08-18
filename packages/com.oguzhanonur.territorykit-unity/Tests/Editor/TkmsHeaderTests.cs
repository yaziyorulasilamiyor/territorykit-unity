using System;
using NUnit.Framework;
using Unity.Collections;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// Header parsing against payloads written by hand in <see cref="TkmsFixture"/>, and one
    /// rejection test per rule in docs/mesh-format.md that a header alone can decide.
    /// </summary>
    public class TkmsHeaderTests
    {
        private static TkmsHeader ParseBytes(byte[] payload)
        {
            var native = new NativeArray<byte>(payload, Allocator.Temp);
            try
            {
                return TkmsHeader.Parse(native);
            }
            finally
            {
                native.Dispose();
            }
        }

        private static void AssertRejected(byte[] payload, string expectedFragment)
        {
            var ex = Assert.Throws<TkmsFormatException>(() => ParseBytes(payload));
            Assert.That(ex.Message, Does.Contain(expectedFragment),
                "rejection message should say which rule was broken");
        }

        [Test]
        public void ParsesKnownTrianglePayload()
        {
            var header = ParseBytes(TkmsFixture.Triangle());

            Assert.AreEqual(1, header.Version);
            Assert.AreEqual(3, header.VertexCount);
            Assert.AreEqual(3, header.IndexCount);
            Assert.AreEqual(1, header.TriangleCount);
            Assert.IsFalse(header.UsesUInt32Indices);
            Assert.AreEqual(2, header.IndexElementSize);
            Assert.AreEqual(0f, header.MinX);
            Assert.AreEqual(0f, header.MinY);
            Assert.AreEqual(10f, header.MaxX);
            Assert.AreEqual(10f, header.MaxY);

            // 32 header + 3 vertices * 2 floats * 4 bytes + 3 indices * 2 bytes
            Assert.AreEqual(32 + 24 + 6, header.PayloadLength);
            Assert.AreEqual(32 + 24, header.IndexOffset);
        }

        [Test]
        public void ReadsUInt32FlagFromThePayloadRatherThanTheVertexCount()
        {
            // A small mesh that nonetheless declares uint32 indices is legal: the flag is
            // authoritative, and a reader that re-derived it from the vertex count would read
            // the index section at the wrong stride.
            var payload = TkmsFixture.Build(
                TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                new TkmsFixture.Options { ForceUInt32Indices = true });

            var header = ParseBytes(payload);

            Assert.IsTrue(header.UsesUInt32Indices);
            Assert.AreEqual(4, header.IndexElementSize);
            Assert.AreEqual(32 + 24 + 12, header.PayloadLength);
        }

        [Test]
        public void IgnoresUndefinedFlagBits()
        {
            // docs/mesh-format.md: a v1 reader ignores bits it does not know instead of
            // rejecting them, so v1-compatible extensions stay readable.
            var payload = TkmsFixture.Build(
                TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                new TkmsFixture.Options { ExtraFlags = 0b1010_1010_1010_1010 & ~1 });

            var header = ParseBytes(payload);

            Assert.IsFalse(header.UsesUInt32Indices, "bit 0 was not set, so indices stay uint16");
            Assert.AreEqual(32 + 24 + 6, header.PayloadLength);
        }

        [Test]
        public void AcceptsTrailingBytesPastTheDeclaredLength()
        {
            var payload = TkmsFixture.Build(
                TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                new TkmsFixture.Options { TrailingBytes = 7 });

            var header = ParseBytes(payload);

            Assert.AreEqual(32 + 24 + 6, header.PayloadLength,
                "declared length is unchanged; the padding is simply not part of the mesh");
            Assert.AreEqual(header.PayloadLength + 7, payload.Length);
        }

        [Test]
        public void RejectsPayloadShorterThanTheDeclaredLength()
        {
            // The asymmetry with trailing bytes is deliberate: extra bytes are padding, missing
            // bytes are a truncated mesh.
            AssertRejected(
                TkmsFixture.Build(TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                    new TkmsFixture.Options { TruncateBytes = 2 }),
                "the header declares");
        }

        [Test]
        public void RejectsPayloadShorterThanTheHeader()
        {
            AssertRejected(new byte[] { (byte)'T', (byte)'K', (byte)'M' }, "shorter than the 32-byte header");
        }

        [Test]
        public void RejectsBadMagic()
        {
            AssertRejected(
                TkmsFixture.Build(TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                    new TkmsFixture.Options { Magic = "TKMX" }),
                "bad magic");
        }

        [Test]
        public void RejectsUnsupportedVersion()
        {
            AssertRejected(
                TkmsFixture.Build(TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                    new TkmsFixture.Options { Version = 2 }),
                "unsupported TKMS version 2");
        }

        [Test]
        public void RejectsIndexCountThatIsNotAMultipleOfThree()
        {
            AssertRejected(
                TkmsFixture.Build(TkmsFixture.TriangleVertices, new[] { 0, 1 }),
                "not a multiple of 3");
        }

        [Test]
        public void RejectsEmptyGeometry()
        {
            AssertRejected(
                TkmsFixture.Build(TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                    new TkmsFixture.Options { IndexCountOverride = 0 }),
                "at least one triangle");
        }

        [Test]
        public void RejectsLargeVertexCountWithoutTheUInt32Flag()
        {
            // The one rule that catches an encoder which forgot to set the flag; without it the
            // index buffer would silently address the wrong vertices.
            AssertRejected(
                TkmsFixture.Build(TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                    new TkmsFixture.Options { VertexCountOverride = 65536 }),
                "uint32 index flag is not set");
        }

        [Test]
        public void RejectsCountsThatDoNotFitASignedInt()
        {
            AssertRejected(
                TkmsFixture.Build(TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                    new TkmsFixture.Options
                    {
                        ForceUInt32Indices = true,
                        VertexCountOverride = 0xFFFFFFF0
                    }),
                "out of range");
        }

        [Test]
        public void RejectsNonFiniteBbox()
        {
            AssertRejected(
                TkmsFixture.Build(TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                    new TkmsFixture.Options
                    {
                        BboxOverride = new[] { 0f, 0f, float.NaN, 10f }
                    }),
                "NaN or infinity");
        }

        [Test]
        public void RejectsInvertedBbox()
        {
            AssertRejected(
                TkmsFixture.Build(TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                    new TkmsFixture.Options
                    {
                        BboxOverride = new[] { 10f, 10f, 0f, 0f }
                    }),
                "inverted");
        }

        [Test]
        public void RejectsUninitialisedPayload()
        {
            var ex = Assert.Throws<TkmsFormatException>(
                () => TkmsHeader.Parse(default(NativeArray<byte>)));
            Assert.That(ex.Message, Does.Contain("shorter than"));
        }

        [Test]
        public void ParsesTheUInt32FixtureUsedByTheDecoderTests()
        {
            // 21846 triangles = 65538 vertices, three past the uint16 ceiling.
            var header = ParseBytes(TkmsFixture.LargeUInt32Mesh(21846));

            Assert.AreEqual(65538, header.VertexCount);
            Assert.IsTrue(header.UsesUInt32Indices,
                "the fixture builder must set bit 0 once the vertex count requires it");
            Assert.AreEqual(4, header.IndexElementSize);
        }
    }
}
