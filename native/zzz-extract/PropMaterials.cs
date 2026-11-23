using AnimeStudio;

namespace ModViewer.ZzzExtract;

// Texture slots of uncaptured props (weapons). Pointers resolve only inside the loaded
// bundle; external ones stay descriptors for the metadata index to locate.
internal static class PropMaterials
{
    private static string Cab<T>(SerializedFile owner, PPtr<T> pointer) where T : AnimeStudio.Object
    {
        if (pointer.m_FileID < 0 || pointer.m_FileID > owner.m_Externals.Count) throw new InvalidDataException();
        return pointer.m_FileID == 0 ? owner.fileName : owner.m_Externals[pointer.m_FileID - 1].fileName;
    }

    private static object Reference<T>(SerializedFile owner, PPtr<T> pointer, IReadOnlyList<SerializedFile> files)
        where T : AnimeStudio.Object
    {
        string cab = Cab(owner, pointer);
        var matches = files.Select((file, ordinal) => (file, ordinal)).Where(item => item.file.fileName == cab).ToArray();
        if (matches.Length == 1)
            return new { cab, path_id = pointer.m_PathID, file_ordinal = matches[0].ordinal, offset = matches[0].file.offset };
        return new { cab, path_id = pointer.m_PathID };
    }

    internal static object Renderer(SkinnedMeshRenderer renderer, IReadOnlyList<SerializedFile> files, Game game)
    {
        if (renderer.m_Materials.Count > 64) throw new InvalidDataException();
        var visible = Visible(renderer, game, files, out var animator);
        return new
        {
            materials = renderer.m_Materials.Select(pointer =>
            {
                var reference = Reference(renderer.assetsFile, pointer, files);
                var file = files.Where(item => item.fileName == Cab(renderer.assetsFile, pointer)).ToArray();
                if (pointer.m_PathID == 0 || file.Length != 1) return (object)new { pointer = reference };
                var objects = file[0].m_Objects.Where(o => o.m_PathID == pointer.m_PathID).ToArray();
                if (objects.Length != 1 || (ClassIDType)objects[0].classID != ClassIDType.Material) throw new InvalidDataException();
                return Describe(new Material(new ObjectReader(file[0].reader, file[0], objects[0], game)), files, reference);
            }).ToArray(),
            visible,
            // The Animator the prefab's renderer belongs to: its GameObject name and controller.
            animator = game.Type == GameType.GI ? null : animator
        };
    }

    // Whether the prefab draws the renderer by default: it is enabled and its GameObject and
    // every ancestor up to and including the Animator's are active. Null when the renderer or
    // a GameObject's serialized layout does not prove those flags (each must be consumed exactly).
    private static bool? Visible(SkinnedMeshRenderer renderer, Game game, IReadOnlyList<SerializedFile> files, out object? animator)
    {
        animator = null;
        if (renderer.m_Enabled is not bool enabled) return null;
        var file = renderer.assetsFile;
        T? Read<T>(long pathId, ClassIDType kind, Func<ObjectReader, T> create) where T : class
        {
            var objects = file.m_Objects.Where(o => o.m_PathID == pathId).ToArray();
            if (pathId == 0 || objects.Length != 1 || (ClassIDType)objects[0].classID != kind) return null;
            return create(new ObjectReader(file.reader, file, objects[0], game));
        }
        if (renderer.m_GameObject.m_FileID != 0) return null;
        long node = renderer.m_GameObject.m_PathID;
        bool active = true;
        var seen = new HashSet<long>();
        for (int depth = 0; depth < 128; depth++)
        {
            if (!seen.Add(node)) return null;
            bool? flag = null;
            var gameObject = Read(node, ClassIDType.GameObject, reader =>
            {
                var value = new GameObject(reader);
                _ = reader.ReadUInt16(); // m_Tag
                bool isActive = reader.ReadBoolean();
                // Genshin omits the trailing alignment of the last field.
                if (reader.BytesLeft() > 0) reader.AlignStream();
                if (reader.BytesLeft() == 0) flag = isActive;
                return value;
            });
            if (gameObject is null || flag is not bool nodeActive) return null;
            active &= nodeActive;
            var components = gameObject.m_Components.Where(p => p.m_FileID == 0)
                .Select(p => file.m_Objects.FirstOrDefault(o => o.m_PathID == p.m_PathID)).ToArray();
            if (components.Length != gameObject.m_Components.Count || components.Any(o => o is null)) return null;
            var animators = components.Where(o => (ClassIDType)o!.classID == ClassIDType.Animator).ToArray();
            if (animators.Length > 0)
            {
                // ZZZ props learn which controller plays each prefab; an Animator layout the
                // reader does not prove leaves it unknown and the visibility answer unchanged.
                if (animators.Length == 1 && game.Type != GameType.GI)
                {
                    try
                    {
                        var component = Read(animators[0]!.m_PathID, ClassIDType.Animator, reader => new Animator(reader));
                        if (component is not null)
                            animator = new { name = gameObject.m_Name, controller = Reference(file, component.m_Controller, files) };
                    }
                    catch (Exception error) when (error is InvalidDataException or IOException or ArgumentException
                        or IndexOutOfRangeException or EndOfStreamException)
                    {
                        animator = null;
                    }
                }
                return enabled && active;
            }
            var transforms = components.Where(o => (ClassIDType)o!.classID == ClassIDType.Transform).ToArray();
            if (transforms.Length != 1) return null;
            var transform = Read(transforms[0]!.m_PathID, ClassIDType.Transform, reader => new Transform(reader));
            if (transform is null || transform.m_Father.m_FileID != 0) return null;
            var parent = Read(transform.m_Father.m_PathID, ClassIDType.Transform, reader => new Transform(reader));
            if (parent is null || parent.m_GameObject.m_FileID != 0) return null;
            node = parent.m_GameObject.m_PathID;
        }
        return null;
    }

    // Distinct serialized files a renderer's materials live in (null pointers excluded).
    internal static string[] MaterialFiles(SkinnedMeshRenderer renderer)
    {
        if (renderer.m_Materials.Count > 64) throw new InvalidDataException();
        return renderer.m_Materials.Where(pointer => pointer.m_PathID != 0)
            .Select(pointer => Cab(renderer.assetsFile, pointer)).Distinct().ToArray();
    }

    // Distinct serialized files a material's textures live in, or null when the material
    // cannot be read; scans must not fail on materials no character uses.
    internal static string[]? TextureFiles(ObjectReader reader)
    {
        try
        {
            var material = new Material(reader);
            var textures = material.m_SavedProperties.m_TexEnvs;
            if (textures.Count > 256) return null;
            return textures.Where(entry => entry.Value.m_Texture.m_PathID != 0)
                .Select(entry => Cab(material.assetsFile, entry.Value.m_Texture)).Distinct().ToArray();
        }
        catch (Exception)
        {
            return null;
        }
    }

    internal static object Describe(Material material, IReadOnlyList<SerializedFile> files, object? pointer = null)
    {
        var textures = material.m_SavedProperties.m_TexEnvs;
        if (textures.Count > 256) throw new InvalidDataException();
        return new
        {
            pointer,
            textures = textures.Where(entry => entry.Value.m_Texture.m_PathID != 0).Select(entry => new
            {
                name = entry.Key,
                texture = Reference(material.assetsFile, entry.Value.m_Texture, files)
            })
        };
    }

    // Raw image bytes (all mips, as stored) go to a work file; the caller keeps the top mip.
    internal static object Texture(Texture2D texture, string work, int id)
    {
        if (texture.m_Width is < 1 or > 16384 || texture.m_Height is < 1 or > 16384) throw new InvalidDataException();
        var data = texture.image_data.GetData();
        if (data.Length is < 1 or > 256 * 1024 * 1024) throw new InvalidDataException();
        string file = $"texture-{id}.bin";
        File.WriteAllBytes(Path.Combine(work, file), data);
        return new { width = texture.m_Width, height = texture.m_Height, format = (int)texture.m_TextureFormat, file };
    }
}
