using AnimeStudio;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System.Runtime.InteropServices;

namespace ModViewer.ZzzExtract;

internal static class Program
{
    private sealed class NameProbe(ObjectReader reader) : NamedObject(reader);
    public static async Task<int> Main(string[] args)
    {
        if (args is ["--self-test"])
        {
            var decoder = NativeLibrary.Load(Path.Combine(AppContext.BaseDirectory, "AnimeStudio.ACL.ZZZV2.dll"));
            foreach (var name in new[] { "DecompressTracksZZZ", "GetLastDecompressError", "Dispose" })
                _ = NativeLibrary.GetExport(decoder, name);
            NativeLibrary.Free(decoder);
            var database = NativeLibrary.Load(Path.Combine(AppContext.BaseDirectory, "AnimeStudio.ACL.DB.dll"));
            foreach (var name in new[] { "DecompressTracks", "Dispose" })
                _ = NativeLibrary.GetExport(database, name);
            NativeLibrary.Free(database);
            var oodle = NativeLibrary.Load(Path.Combine(AppContext.BaseDirectory, "oodle-data-shared.dll"));
            _ = NativeLibrary.GetExport(oodle, "OodleLZ_Decompress");
            NativeLibrary.Free(oodle);
            _ = GameManager.GetGame(GameType.ZZZ);
            _ = GameManager.GetGame(GameType.GI);
            AvatarPaths.SelfTest();
            Console.WriteLine("ZZZ and GI extraction ABI checks passed.");
            return 0;
        }
        if (args is not [("--zzz" or "--gi") and var kind, var root, "--work", var work]) return 2;
        root = Path.GetFullPath(root);
        work = Path.GetFullPath(work);
        Console.SetOut(TextWriter.Null);
        Console.SetError(TextWriter.Null);
        var game = GameManager.GetGame(kind == "--gi" ? GameType.GI : GameType.ZZZ);
        var label = game.Type == GameType.GI ? "Genshin Impact" : "ZZZ";
        var blendShapeClamp = new Lazy<bool?>(() => BlendShapePolicy.Read(root, game));
        string? line;
        while ((line = await Console.In.ReadLineAsync()) is not null)
        {
            if (line.Length > 256 * 1024) return 2;
            var request = JObject.Parse(line);
            int id = (int?)request["id"] ?? 0;
            if (id < 1) return 2;
            object response;
            try { response = Execute(root, work, game, request, blendShapeClamp); }
            catch (Exception error) { response = new { error = label + " data could not be read.", category = error.GetType().Name }; }
            var target = Path.Combine(work, $"command-{id}.json");
            await File.WriteAllTextAsync(target + ".tmp", JsonConvert.SerializeObject(response));
            File.Move(target + ".tmp", target);
        }
        return 0;
    }

    private static readonly string[] ZzzBlocks = ["ZenlessZoneZero_Data/StreamingAssets/Blocks/", "ZenlessZoneZero_Data/Persistent/Blocks/"];
    // Genshin keeps one directory level below each tree (blocks/NN/name.blk).
    private static readonly string[] GiBlocks =
        ["GenshinImpact_Data/StreamingAssets/AssetBundles/blocks/", "GenshinImpact_Data/Persistent/AssetBundles/blocks/"];

    private static string Block(string root, string relative, bool gi)
    {
        if (Path.IsPathRooted(relative) || relative.Split('/', '\\').Any(p => p is ".." or ".")) throw new InvalidDataException();
        var path = Path.GetFullPath(Path.Combine(root, relative));
        var normalized = relative.Replace('\\', '/');
        var prefix = (gi ? GiBlocks : ZzzBlocks).FirstOrDefault(p => normalized.StartsWith(p, StringComparison.OrdinalIgnoreCase));
        if (prefix is null || (gi && normalized[prefix.Length..].Count(c => c == '/') != 1)
            || Path.GetExtension(path) != ".blk" || !File.Exists(path)) throw new InvalidDataException();
        ValidateFile(root, path);
        return path;
    }

    internal static void ValidateFile(string root, string path)
    {
        for (var entry = new FileInfo(path) as FileSystemInfo; entry is not null; entry = Directory.GetParent(entry.FullName))
        {
            if ((entry.Attributes & FileAttributes.ReparsePoint) != 0) throw new InvalidDataException();
            if (string.Equals(entry.FullName, root, StringComparison.OrdinalIgnoreCase)) break;
        }
    }

    private static object Execute(string root, string work, Game game, JObject request, Lazy<bool?> blendShapeClamp)
    {
        string operation = (string?)request["op"] ?? "";
        if (operation == "scan_batch")
        {
            var blocks = request["blocks"]?.ToObject<string[]>() ?? throw new InvalidDataException();
            if (blocks.Length is < 1 or > 16 || blocks.Distinct().Count() != blocks.Length) throw new InvalidDataException();
            return new
            {
                items = blocks.Select(block => new
                {
                    block,
                    rows = JObject.FromObject(
                Execute(root, work, game, new JObject { ["op"] = "scan", ["block"] = block }, blendShapeClamp))["rows"]
                }).ToArray()
            };
        }
        if (operation is not ("scan" or "read")) throw new InvalidDataException();
        bool gi = game.Type == GameType.GI;
        var block = Block(root, (string?)request["block"] ?? "", gi);
        var manager = new AssetsManager { Game = game, Silent = true, SkipProcess = true, ResolveDependencies = false };
        var rows = new List<object>();
        object? exported = null;
        Exception? failure = null;
        int groups = 0;
        if (operation == "read") manager.FilterData.Items.Add(new AssetsManager.AssetFilterDataItem
        { Source = block, Offset = (long?)request["offset"] ?? throw new InvalidDataException() });
        manager.AfterBundleLoaded = () =>
        {
            groups++;
            try
            {
                for (int ordinal = 0; ordinal < manager.assetsFileList.Count; ordinal++)
                {
                    var file = manager.assetsFileList[ordinal];
                    foreach (var info in file.m_Objects)
                    {
                        var type = (ClassIDType)info.classID;
                        // Renderer routes prove facial curve targets in both games; only GI indexes controllers.
                        if (type is not (ClassIDType.Mesh or ClassIDType.Avatar or ClassIDType.AnimationClip
                            or ClassIDType.SkinnedMeshRenderer) && !(gi && type == ClassIDType.AnimatorController)) continue;
                        if (operation == "read" && (file.fileName != (string?)request["cab"]
                            || ordinal != (int?)request["file_ordinal"] || info.m_PathID != (long?)request["path_id"])) continue;
                        var reader = new ObjectReader(file.reader, file, info, game);
                        if (type == ClassIDType.SkinnedMeshRenderer)
                        {
                            if (operation != "scan") throw new InvalidDataException();
                            var renderer = MorphSources.Renderer(new SkinnedMeshRenderer(reader), manager.assetsFileList, game);
                            rows.Add(new
                            {
                                name = "",
                                type = type.ToString(),
                                offset = file.offset,
                                cab = file.fileName,
                                file_ordinal = ordinal,
                                path_id = info.m_PathID,
                                renderer
                            });
                            continue;
                        }
                        var named = new NameProbe(reader);
                        if (operation == "scan")
                        {
                            long[][]? shape = null;
                            if (type == ClassIDType.Mesh)
                            {
                                var mesh = new Mesh(reader, decodeGeometry: false);
                                shape = mesh.m_SubMeshes.Select(s => new[] { (long)s.vertexCount, s.indexCount }).ToArray();
                            }
                            rows.Add(new
                            {
                                name = named.m_Name,
                                type = type.ToString(),
                                offset = file.offset,
                                cab = file.fileName,
                                file_ordinal = ordinal,
                                path_id = info.m_PathID,
                                shape
                            });
                        }
                        else
                        {
                            if (exported is not null) throw new InvalidDataException();
                            exported = (gi, type) switch
                            {
                                (false, ClassIDType.Mesh) => ExportMesh(new Mesh(reader)),
                                (false, ClassIDType.Avatar) => ReadAvatar(reader),
                                (false, ClassIDType.AnimationClip) => ExportClip(new AnimationClip(reader), work, (int)request["id"]!, blendShapeClamp.Value),
                                (true, ClassIDType.Mesh) => GiExport.ExportMesh(new Mesh(reader)),
                                (true, ClassIDType.Avatar) => GiExport.ReadAvatar(new Avatar(reader)),
                                (true, ClassIDType.AnimationClip) => GiExport.ExportClip(new AnimationClip(reader), work, (int)request["id"]!, blendShapeClamp.Value),
                                (true, ClassIDType.AnimatorController) => GiExport.ReadController(new AnimatorController(reader), file),
                                _ => throw new InvalidDataException()
                            };
                        }
                    }
                }
            }
            catch (Exception error) { failure = error; }
            finally { manager.ClearLoadedAssets(); }
        };
        try { manager.LoadFiles([block], mergeSplitAssets: false); }
        finally { manager.Clear(); }
        if (failure is not null) throw failure;
        if (groups == 0) throw new InvalidDataException();
        if (operation == "scan") return new { rows };
        return exported ?? throw new InvalidDataException();
    }

    internal static object ExportMesh(Mesh mesh) => new
    {
        name = mesh.m_Name,
        blend_shapes = MorphSources.Shapes(mesh),
        vertices = mesh.m_Vertices,
        uv = mesh.m_UV0,
        indices = mesh.m_Indices,
        weights = mesh.m_Skin.Select(s => s.weight),
        bone_indices = mesh.m_Skin.Select(s => s.boneIndex),
        bone_hashes = mesh.m_BoneNameHashes,
        bind_poses = mesh.m_BindPose.Select(m => Enumerable.Range(0, 16).Select(i => m[i]).ToArray()),
        sections = mesh.m_SubMeshes.Select((s, i) => new
        {
            first = mesh.m_SubMeshes.Take(i).Sum(p => (long)p.indexCount),
            count = s.indexCount,
            vertex_first = s.firstVertex,
            vertex_count = s.vertexCount
        })
    };

    private static object ReadAvatar(ObjectReader reader)
    {
        var named = new NameProbe(reader);
        _ = reader.ReadUInt32();
        var avatar = new AvatarConstant(reader, isZZZ: true, readRootMotionFlag: false);
        var skeleton = avatar.m_AvatarSkeleton;
        var poses = avatar.m_AvatarSkeletonPose.m_X;
        var paths = AvatarPaths.Read(reader, reader.byteStart + reader.byteSize, skeleton.m_ID);
        if (skeleton.m_Node.Count != skeleton.m_ID.Length || poses.Length != skeleton.m_ID.Length)
            throw new InvalidDataException();
        return new
        {
            name = named.m_Name,
            bones = skeleton.m_Node.Select((node, i) => new
            {
                path = paths[skeleton.m_ID[i]],
                hash = skeleton.m_ID[i],
                parent = node.m_ParentId,
                translation = new[] { poses[i].t.X, poses[i].t.Y, poses[i].t.Z },
                rotation = new[] { poses[i].q.X, poses[i].q.Y, poses[i].q.Z, poses[i].q.W },
                scale = new[] { poses[i].s.X, poses[i].s.Y, poses[i].s.Z }
            })
        };
    }

    private static object ExportClip(AnimationClip animation, string work, int id, bool? blendShapeClamp)
    {
        var clip = animation.m_MuscleClip.m_Clip;
        if (clip.m_ACLClip is not ZZZACLClip acl) throw new InvalidDataException();
        var (values, times) = acl.IsSet ? NativeAcl.Decode(acl) : (Array.Empty<float>(), Array.Empty<float>());
        string file = $"samples-{id}.bin";
        using (var writer = new BinaryWriter(File.Create(Path.Combine(work, file))))
            foreach (float value in values) writer.Write(value);
        return new
        {
            name = animation.m_Name,
            blend_shape_clamp = blendShapeClamp,
            start = animation.m_MuscleClip.m_StartTime,
            stop = animation.m_MuscleClip.m_StopTime,
            sample_rate = animation.m_SampleRate,
            file,
            times,
            acl_count = acl.CurveCount,
            bindings = animation.m_ClipBindingConstant.genericBindings.Select(b => new
            { path = b.path, attribute = b.attribute, type = b.typeID.ToString(), custom = b.customType, pointer = b.isPPtrCurve }),
            streamed_count = clip.m_StreamedClip.curveCount,
            streamed = clip.m_StreamedClip.ReadData().Select(f => new
            {
                time = f.time,
                keys = f.keyList.Select(k => new { index = k.index, value = k.value, incoming = k.inSlope, outgoing = k.outSlope })
            }),
            dense = new
            {
                count = clip.m_DenseClip.m_CurveCount,
                frames = clip.m_DenseClip.m_FrameCount,
                rate = clip.m_DenseClip.m_SampleRate,
                start = clip.m_DenseClip.m_BeginTime,
                values = clip.m_DenseClip.m_SampleArray
            },
            constant = clip.m_ConstantClip.data
        };
    }
}

// ABI and aligned buffer ownership follow AnimeStudio's MIT-licensed DBACL wrapper.
internal static class NativeAcl
{
    [StructLayout(LayoutKind.Sequential)]
    private struct Samples { public nint Values; public int Count; public nint Times; public int Frames; }
    [DllImport("AnimeStudio.ACL.ZZZV2", CallingConvention = CallingConvention.Cdecl, EntryPoint = "DecompressTracksZZZ")]
    private static extern void Decode(nint t, int tl, nint s, int sl, nint d, int dl, nint b, int bl, ref Samples output);
    [DllImport("AnimeStudio.ACL.ZZZV2", CallingConvention = CallingConvention.Cdecl, EntryPoint = "GetLastDecompressError")]
    private static extern nint Error();
    [DllImport("AnimeStudio.ACL.ZZZV2", CallingConvention = CallingConvention.Cdecl, EntryPoint = "Dispose")]
    private static extern void Dispose(ref Samples output);

    internal static (float[], float[]) Decode(ZZZACLClip clip)
    {
        byte[][] data = [clip.m_TransformData ?? [], clip.m_ScalarData ?? [], clip.m_databaseData ?? [], clip.m_DatabaseData ?? []];
        nint[] allocations = new nint[4], aligned = new nint[4];
        var output = new Samples();
        try
        {
            for (int i = 0; i < 4; i++)
                if (data[i].Length > 0)
                {
                    allocations[i] = Marshal.AllocHGlobal(data[i].Length + 15);
                    aligned[i] = (allocations[i] + 15) & ~15;
                    Marshal.Copy(data[i], 0, aligned[i], data[i].Length);
                }
            Decode(aligned[0], data[0].Length, aligned[1], data[1].Length,
                aligned[2], data[2].Length, aligned[3], data[3].Length, ref output);
            if (!string.IsNullOrEmpty(Marshal.PtrToStringAnsi(Error())) || output.Count <= 0 || output.Frames <= 0
                || output.Count > 32 * 1024 * 1024 || output.Frames > 100000) throw new InvalidDataException();
            var values = new float[output.Count]; var times = new float[output.Frames];
            Marshal.Copy(output.Values, values, 0, values.Length);
            Marshal.Copy(output.Times, times, 0, times.Length);
            return (values, times);
        }
        finally
        {
            Dispose(ref output);
            foreach (var allocation in allocations) Marshal.FreeHGlobal(allocation);
        }
    }
}
