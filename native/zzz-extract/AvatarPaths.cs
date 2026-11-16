using AnimeStudio;
using SevenZip;
using System.Text;

namespace ModViewer.ZzzExtract;

internal static class AvatarPaths
{
    private static readonly UTF8Encoding Utf8 = new(false, true);

    internal static Dictionary<uint, string> Read(EndianBinaryReader reader, long end, uint[] required)
    {
        long start = reader.Position;
        if (start % 4 != 0 || end > reader.Length || end - start < 4 || required.Length is < 1 or > 4096
            || required.Distinct().Count() != required.Length) throw new InvalidDataException();
        Dictionary<uint, string>? chosen = null;
        long chosenEnd = 0;
        int valid = 0;
        // Older constants end at the path table; newer ones insert one Boolean
        // and alignment padding. Only a complete hash-verified table proves a layout.
        foreach (bool flag in new[] { false, true })
        {
            reader.Position = start;
            try
            {
                if (flag)
                {
                    if (reader.ReadByte() > 1) continue;
                    reader.AlignStream();
                }
                if (end - reader.Position < 4) continue;
                int count = reader.ReadInt32();
                if (count < required.Length || count > 4096) continue;
                var paths = new Dictionary<uint, string>();
                for (int i = 0; i < count; i++)
                {
                    if (end - reader.Position < 8) throw new InvalidDataException();
                    uint hash = reader.ReadUInt32();
                    int length = reader.ReadInt32();
                    if (length is < 0 or > 4096 || length > end - reader.Position) throw new InvalidDataException();
                    string path = Utf8.GetString(reader.ReadBytes(length));
                    reader.AlignStream();
                    if (reader.Position > end || CRC.CalculateDigestUTF8(path) != hash || !paths.TryAdd(hash, path))
                        throw new InvalidDataException();
                }
                if (required.Any(hash => !paths.ContainsKey(hash))) continue;
                chosen = paths;
                chosenEnd = reader.Position;
                valid++;
            }
            catch (InvalidDataException) { }
            catch (IOException) { }
            catch (ArgumentException) { }
        }
        if (valid != 1 || chosen is null) throw new InvalidDataException("The Avatar path table layout is invalid or ambiguous.");
        reader.Position = chosenEnd;
        return chosen;
    }

    internal static void SelfTest()
    {
        static (byte[], uint[], long) Fixture(int count, byte? flag, bool duplicate = false)
        {
            using var stream = new MemoryStream();
            using var writer = new BinaryWriter(stream);
            writer.Write(0);
            if (flag.HasValue) writer.Write(new byte[] { flag.Value, 0, 0, 0 });
            writer.Write(count);
            var hashes = new List<uint>();
            for (int i = 0; i < count; i++)
            {
                string path = i == 0 || duplicate ? "" : $"Root/Joint{i:D3}";
                uint hash = CRC.CalculateDigestUTF8(path);
                hashes.Add(hash);
                byte[] text = Utf8.GetBytes(path);
                writer.Write(hash);
                writer.Write(text.Length);
                writer.Write(text);
                while (stream.Position % 4 != 0) writer.Write((byte)0);
            }
            long end = stream.Position;
            writer.Write(new byte[16]); // Optional authoring data is outside the table.
            return (stream.ToArray(), hashes.ToArray(), end);
        }
        static void Check(byte[] bytes, uint[] hashes, long end, bool accepted)
        {
            using var reader = new EndianBinaryReader(new MemoryStream(bytes), EndianType.LittleEndian);
            reader.Position = 4;
            try
            {
                var paths = Read(reader, end, hashes);
                if (!accepted || paths.Count != hashes.Length || reader.Position != end) throw new InvalidOperationException();
            }
            catch (InvalidDataException) when (!accepted) { }
        }
        foreach (int count in new[] { 1, 256 })
            foreach (byte? flag in new byte?[] { null, 0, 1 })
            {
                var (bytes, hashes, end) = Fixture(count, flag);
                Check(bytes, hashes, end, true);
                Check(bytes, hashes, end - 1, false);
                Check(bytes, hashes.Skip(1).Append(123u).ToArray(), end, false);
                bytes[flag.HasValue ? 12 : 8] ^= 1;
                Check(bytes, hashes, end, false);
            }
        var (duplicated, ids, limit) = Fixture(2, null, duplicate: true);
        Check(duplicated, ids.Distinct().ToArray(), limit, false);
        var (invalid, required, bound) = Fixture(2, null);
        invalid[24] = 0xff;
        Check(invalid, required, bound, false);
    }
}
