using AnimeStudio;

namespace ModViewer.ZzzExtract;

// Resolve only explicit Unity pointers in the currently loaded bundle. External
// references stay descriptors for the complete metadata index to disambiguate.
internal static class MorphSources
{
    internal static object Shapes(Mesh mesh)
    {
        var data = mesh.m_Shapes;
        if (data.channels.Count > 256 || data.vertices.Count > 4_000_000) throw new InvalidDataException();
        return new
        {
            channels = data.channels.Select(c => new { name = c.name, hash = c.nameHash, first = c.frameIndex, count = c.frameCount }),
            weights = data.fullWeights,
            frames = data.shapes.Select(s => new { first = s.firstVertex, count = s.vertexCount, normals = s.hasNormals }),
            vertices = data.vertices.Select(v => new
            {
                index = v.index,
                position = new[] { v.vertex.X, v.vertex.Y, v.vertex.Z },
                normal = new[] { v.normal.X, v.normal.Y, v.normal.Z }
            })
        };
    }

    internal static object? Renderer(SkinnedMeshRenderer renderer, IReadOnlyList<SerializedFile> files, Game game)
    {
        var cache = new Dictionary<(SerializedFile, long), AnimeStudio.Object>();
        SerializedFile? File<T>(SerializedFile owner, PPtr<T> pointer) where T : AnimeStudio.Object
        {
            if (pointer.m_FileID == 0) return owner;
            if (pointer.m_FileID < 1 || pointer.m_FileID > owner.m_Externals.Count) return null;
            string cab = owner.m_Externals[pointer.m_FileID - 1].fileName;
            var matches = files.Where(f => f.fileName == cab).ToArray();
            return matches.Length == 1 ? matches[0] : null;
        }
        object Reference<T>(SerializedFile owner, PPtr<T> pointer) where T : AnimeStudio.Object
        {
            var file = File(owner, pointer);
            if (file is not null)
                return new { cab = file.fileName, path_id = pointer.m_PathID, file_ordinal = files.ToList().IndexOf(file), offset = file.offset };
            if (pointer.m_FileID < 1 || pointer.m_FileID > owner.m_Externals.Count) throw new InvalidDataException();
            return new { cab = owner.m_Externals[pointer.m_FileID - 1].fileName, path_id = pointer.m_PathID };
        }
        AnimeStudio.Object? Read<T>(SerializedFile owner, PPtr<T> pointer) where T : AnimeStudio.Object
        {
            var file = File(owner, pointer);
            if (file is null || pointer.m_PathID == 0) return null;
            var key = (file, pointer.m_PathID);
            if (cache.TryGetValue(key, out var cached)) return cached;
            if (cache.Count >= 4096) throw new InvalidDataException();
            var objects = file.m_Objects.Where(o => o.m_PathID == pointer.m_PathID).ToArray();
            if (objects.Length != 1) throw new InvalidDataException();
            var reader = new ObjectReader(file.reader, file, objects[0], game);
            AnimeStudio.Object? value = (ClassIDType)objects[0].classID switch
            {
                ClassIDType.GameObject => new GameObject(reader),
                ClassIDType.Transform => new Transform(reader),
                ClassIDType.Animator => new Animator(reader),
                _ => null
            };
            if (value is not null) cache.Add(key, value);
            return value;
        }
        var node = Read(renderer.assetsFile, renderer.m_GameObject) as GameObject;
        var names = new List<string>();
        var seen = new HashSet<(SerializedFile, long)>();
        for (int depth = 0; node is not null && depth < 128; depth++)
        {
            if (!seen.Add((node.assetsFile, node.m_PathID))) throw new InvalidDataException();
            var components = node.m_Components.Select(p => Read(node.assetsFile, p)).ToArray();
            var animators = components.OfType<Animator>().ToArray();
            if (animators.Length > 1) throw new InvalidDataException();
            if (animators.Length == 1)
            {
                names.Reverse();
                string? BonePath(PPtr<Transform> pointer)
                {
                    var transform = Read(renderer.assetsFile, pointer) as Transform;
                    var parts = new List<string>();
                    var visited = new HashSet<(SerializedFile, long)>();
                    for (int step = 0; transform is not null && step < 128; step++)
                    {
                        if (!visited.Add((transform.assetsFile, transform.m_PathID))) return null;
                        var owner = Read(transform.assetsFile, transform.m_GameObject) as GameObject;
                        if (owner is null) return null;
                        if (owner.assetsFile == node.assetsFile && owner.m_PathID == node.m_PathID)
                        {
                            parts.Reverse();
                            return string.Join("/", parts);
                        }
                        if (string.IsNullOrEmpty(owner.m_Name) || owner.m_Name.Contains('/')) return null;
                        parts.Add(owner.m_Name);
                        transform = Read(transform.assetsFile, transform.m_Father) as Transform;
                    }
                    return null;
                }
                if (renderer.m_Bones.Count > 4096) throw new InvalidDataException();
                return new
                {
                    path = string.Join("/", names),
                    mesh = Reference(renderer.assetsFile, renderer.m_Mesh),
                    avatar = Reference(animators[0].assetsFile, animators[0].m_Avatar),
                    bone_paths = renderer.m_Bones.Select(BonePath).ToArray(),
                    blend_shape_count = renderer.m_BlendShapeWeights.Length
                };
            }
            if (string.IsNullOrEmpty(node.m_Name) || node.m_Name.Contains('/')) return null;
            names.Add(node.m_Name);
            var transforms = components.OfType<Transform>().ToArray();
            if (transforms.Length != 1) return null;
            var parent = Read(transforms[0].assetsFile, transforms[0].m_Father) as Transform;
            node = parent is null ? null : Read(parent.assetsFile, parent.m_GameObject) as GameObject;
        }
        return null;
    }
}
