using AnimeStudio;

namespace ModViewer.ZzzExtract;

// Genshin effect prefabs attach their content to a character point through a Follow
// component (MoleMole.MonoEffectPluginFollow) on the prefab root, whose first serialized
// field names the point. Scans report each such root with the Animators beneath it (their
// Avatar and controller pointers), so skill objects can be placed where the game mounts them. Only explicit in-file pointers
// and the script's own class name decide; a field that does not read as a point name is
// reported as null rather than guessed.
internal static class GiFollow
{
    private const string Namespace = "MoleMole";
    private const string ClassName = "MonoEffectPluginFollow";
    private const int MaxPointName = 256;
    private const int MaxAnimators = 64;
    private const int MaxDepth = 64;

    internal static List<object> Rows(SerializedFile file, int ordinal, Game game)
    {
        var rows = new List<object>();
        // A row needs a Follow behaviour and an Animator; the object table tells without reading either.
        if (!file.m_Objects.Any(o => (ClassIDType)o.classID == ClassIDType.Animator)
            || !file.m_Objects.Any(o => (ClassIDType)o.classID == ClassIDType.MonoBehaviour))
            return rows;
        var scripts = new HashSet<long>();
        foreach (var info in file.m_Objects.Where(o => (ClassIDType)o.classID == ClassIDType.MonoScript))
        {
            var script = Try(() => new MonoScript(new ObjectReader(file.reader, file, info, game)));
            if (script is not null && script.m_Namespace == Namespace && script.m_ClassName == ClassName) scripts.Add(info.m_PathID);
        }
        if (scripts.Count == 0) return rows;
        var gameObjects = new Dictionary<long, GameObject>();
        var transforms = new Dictionary<long, Transform>();
        var animators = new List<Animator>();
        var follows = new List<(long PathId, MonoBehaviour Behaviour, string? Point)>();
        foreach (var info in file.m_Objects)
        {
            switch ((ClassIDType)info.classID)
            {
                case ClassIDType.GameObject:
                    if (Try(() => new GameObject(new ObjectReader(file.reader, file, info, game))) is { } gameObject)
                        gameObjects[info.m_PathID] = gameObject;
                    break;
                case ClassIDType.Transform:
                    if (Try(() => new Transform(new ObjectReader(file.reader, file, info, game))) is { } transform)
                        transforms[info.m_PathID] = transform;
                    break;
                case ClassIDType.Animator:
                    if (Try(() => new Animator(new ObjectReader(file.reader, file, info, game))) is { } animator)
                        animators.Add(animator);
                    break;
                case ClassIDType.MonoBehaviour:
                    var reader = new ObjectReader(file.reader, file, info, game);
                    var behaviour = Try(() => new MonoBehaviour(reader));
                    if (behaviour is not null && behaviour.m_Script.m_FileID == 0 && scripts.Contains(behaviour.m_Script.m_PathID))
                        follows.Add((info.m_PathID, behaviour, Try<string>(() => PointName(reader))));
                    break;
            }
        }
        // Each GameObject's own Transform (its father gives the hierarchy).
        var owned = new Dictionary<long, Transform>();
        var shared = new HashSet<long>();
        foreach (var transform in transforms.Values)
            if (transform.m_GameObject.m_FileID == 0 && !owned.TryAdd(transform.m_GameObject.m_PathID, transform))
                shared.Add(transform.m_GameObject.m_PathID);
        foreach (var node in shared) owned.Remove(node);
        foreach (var (pathId, behaviour, point) in follows)
        {
            if (behaviour.m_GameObject.m_FileID != 0 || !gameObjects.TryGetValue(behaviour.m_GameObject.m_PathID, out var root)
                || !owned.TryGetValue(behaviour.m_GameObject.m_PathID, out var rootTransform) || rootTransform.m_Father.m_PathID != 0)
                continue;
            var held = new List<object>();
            foreach (var animator in animators)
            {
                // An Animator is known by its Avatar or, for generic rigs without one, by its controller.
                if ((animator.m_Avatar.m_PathID == 0 && animator.m_Controller.m_PathID == 0) || animator.m_GameObject.m_FileID != 0) continue;
                var chain = Chain(animator.m_GameObject.m_PathID, behaviour.m_GameObject.m_PathID, gameObjects, owned, transforms);
                if (chain is null) continue;
                if (held.Count == MaxAnimators) { held = null; break; }
                held.Add(new
                {
                    avatar = animator.m_Avatar.m_PathID == 0 ? null : Reference(file, ordinal, animator.m_Avatar),
                    controller = animator.m_Controller.m_PathID == 0 ? null : Reference(file, ordinal, animator.m_Controller),
                    path = string.Join("/", chain.Select(node => node.Name)),
                    chain = chain.Select(node => new
                    {
                        translation = new[] { node.Transform.m_LocalPosition.X, node.Transform.m_LocalPosition.Y, node.Transform.m_LocalPosition.Z },
                        rotation = new[] { node.Transform.m_LocalRotation.X, node.Transform.m_LocalRotation.Y, node.Transform.m_LocalRotation.Z, node.Transform.m_LocalRotation.W },
                        scale = new[] { node.Transform.m_LocalScale.X, node.Transform.m_LocalScale.Y, node.Transform.m_LocalScale.Z }
                    }).ToArray()
                });
            }
            if (held is null || held.Count == 0) continue;
            rows.Add(new
            {
                name = root.m_Name,
                type = "Follow",
                offset = file.offset,
                cab = file.fileName,
                file_ordinal = ordinal,
                path_id = pathId,
                follow = point,
                enabled = behaviour.m_Enabled == 1,
                animators = held
            });
        }
        return rows;
    }

    // The nodes from the root's child down to the Animator's GameObject, or null when the
    // Animator is not under that root or the hierarchy is not proven in this file.
    private static List<(string Name, Transform Transform)>? Chain(long node, long root, Dictionary<long, GameObject> gameObjects,
        Dictionary<long, Transform> owned, Dictionary<long, Transform> transforms)
    {
        var chain = new List<(string, Transform)>();
        var seen = new HashSet<long>();
        for (int depth = 0; depth < MaxDepth; depth++)
        {
            if (node == root)
            {
                chain.Reverse();
                return chain.Count > 0 ? chain : null;
            }
            if (!seen.Add(node) || !gameObjects.TryGetValue(node, out var gameObject) || !owned.TryGetValue(node, out var transform)
                || string.IsNullOrEmpty(gameObject.m_Name) || gameObject.m_Name.Contains('/') || transform.m_Father.m_FileID != 0
                || !transforms.TryGetValue(transform.m_Father.m_PathID, out var parent) || parent.m_GameObject.m_FileID != 0)
                return null;
            chain.Add((gameObject.m_Name, transform));
            node = parent.m_GameObject.m_PathID;
        }
        return null;
    }

    // The Follow component's first field: a length-prefixed point name (empty follows the
    // character itself). Anything else does not prove a point name.
    private static string? PointName(ObjectReader reader)
    {
        if (reader.BytesLeft() < 4) return null;
        int length = reader.ReadInt32();
        if (length is < 0 or > MaxPointName || length > reader.BytesLeft()) return null;
        var bytes = reader.ReadBytes(length);
        if (bytes.Any(b => b is < 0x21 or > 0x7e)) return null;
        return System.Text.Encoding.ASCII.GetString(bytes);
    }

    private static object Reference<T>(SerializedFile owner, int ordinal, PPtr<T> pointer) where T : AnimeStudio.Object
    {
        if (pointer.m_FileID == 0)
            return new { cab = owner.fileName, path_id = pointer.m_PathID, file_ordinal = ordinal, offset = owner.offset };
        if (pointer.m_FileID < 1 || pointer.m_FileID > owner.m_Externals.Count) throw new InvalidDataException();
        return new { cab = owner.m_Externals[pointer.m_FileID - 1].fileName, path_id = pointer.m_PathID };
    }

    // Discovery reads objects whose layouts the reader may not support; an unreadable
    // object proves nothing and is left out (extraction then keeps its own fallback).
    private static T? Try<T>(Func<T?> read) where T : class
    {
        try { return read(); }
        catch (Exception error) when (error is InvalidDataException or EndOfStreamException or IOException
                                      or ArgumentException or IndexOutOfRangeException or NotSupportedException)
        { return null; }
    }
}
