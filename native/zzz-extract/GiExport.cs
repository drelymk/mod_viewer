using AnimeStudio;
using System.Runtime.InteropServices;

namespace ModViewer.ZzzExtract;

// Genshin Impact (GameType.GI) exports. Locators, bounds and response files are
// shared with the ZZZ mode; only Unity object interpretation differs here.
internal static class GiExport
{
    private const int MaxStates = 4096;
    private const int MaxTransitions = 65536;
    private const int MaxConditions = 64;
    private const int MaxLeaves = 64;
    private const int MaxControllerClips = 8192;

    internal static object ExportMesh(Mesh mesh)
    {
        // Skin data packed into UV2/UV3 or partial skins cannot prove captured weights.
        if (mesh.m_Skin is null || mesh.m_Skin.Count != mesh.m_VertexCount || mesh.m_VertexCount <= 0)
            throw new InvalidDataException();
        return Program.ExportMesh(mesh);
    }

    private static float[] Xyz(object value) => value switch
    {
        Vector3 v => [v.X, v.Y, v.Z],
        Vector4 v => [v.X, v.Y, v.Z],
        _ => throw new InvalidDataException()
    };

    private static float[] Quat(Vector4 v) => [v.X, v.Y, v.Z, v.W];

    private static object Trs(XForm x) => new
    {
        translation = new[] { x.t.X, x.t.Y, x.t.Z },
        rotation = new[] { x.q.X, x.q.Y, x.q.Z, x.q.W },
        scale = new[] { x.s.X, x.s.Y, x.s.Z }
    };

    private static void Indices(int[]? values, int count, int length)
    {
        if (values is null || (length >= 0 && values.Length != length) || values.Any(v => v < -1 || v >= count))
            throw new InvalidDataException();
    }

    internal static object ReadAvatar(Avatar avatar)
    {
        var constant = avatar.m_Avatar;
        var skeleton = constant.m_AvatarSkeleton;
        // Genshin meshes are bound in the model's default pose; the skeleton pose is the
        // Humanoid-normalized one, so only the default pose proves mesh inverse binds.
        var poses = constant.m_DefaultPose?.m_X ?? throw new InvalidDataException();
        int count = skeleton.m_Node.Count;
        if (count < 1 || count > 4096 || skeleton.m_ID.Length != count || poses.Length != count
            || skeleton.m_ID.Distinct().Count() != count)
            throw new InvalidDataException();
        var bones = new object[count];
        for (int i = 0; i < count; i++)
        {
            int parent = skeleton.m_Node[i].m_ParentId;
            if (parent < -1 || parent >= i || !avatar.m_TOS.TryGetValue(skeleton.m_ID[i], out var path))
                throw new InvalidDataException();
            var pose = poses[i];
            bones[i] = new
            {
                path,
                hash = skeleton.m_ID[i],
                parent,
                translation = new[] { pose.t.X, pose.t.Y, pose.t.Z },
                rotation = new[] { pose.q.X, pose.q.Y, pose.q.Z, pose.q.W },
                scale = new[] { pose.s.X, pose.s.Y, pose.s.Z }
            };
        }
        return new { name = avatar.m_Name, bones, human = Human(constant, count) };
    }

    private static object? Human(AvatarConstant constant, int avatarCount)
    {
        var human = constant.m_Human;
        var skeleton = human?.m_Skeleton;
        if (human is null || skeleton is null || skeleton.m_Node.Count == 0) return null;
        int nodes = skeleton.m_Node.Count;
        var axes = skeleton.m_AxesArray ?? [];
        var pose = human.m_SkeletonPose?.m_X ?? [];
        if (nodes > 4096 || axes.Count > nodes || pose.Length != nodes
            || skeleton.m_Node.Select((n, i) => n.m_ParentId < -1 || n.m_ParentId >= i || n.m_AxesId < -1 || n.m_AxesId >= axes.Count).Any(bad => bad)
            // Only the ZYRoll axes type is supported by the Python Humanoid solver.
            || axes.Any(a => a.m_Type != 1))
            throw new InvalidDataException();
        Indices(human.m_HumanBoneIndex, nodes, 25);
        Indices(constant.m_HumanSkeletonIndexArray, avatarCount, nodes);
        var left = human.m_LeftHand?.m_HandBoneIndex ?? [];
        var right = human.m_RightHand?.m_HandBoneIndex ?? [];
        Indices(left, nodes, left.Length == 0 ? 0 : 15);
        Indices(right, nodes, right.Length == 0 ? 0 : 15);
        if (human.m_HumanBoneMass is not { Length: 25 } || !float.IsFinite(human.m_Scale) || human.m_Scale <= 0)
            throw new InvalidDataException();
        return new
        {
            skeleton = new
            {
                parent = skeleton.m_Node.Select(n => n.m_ParentId),
                axes_id = skeleton.m_Node.Select(n => n.m_AxesId)
            },
            axes = axes.Select(a => new
            {
                pre_q = Quat(a.m_PreQ),
                post_q = Quat(a.m_PostQ),
                sgn = Xyz(a.m_Sgn),
                min = Xyz(a.m_Limit.m_Min),
                max = Xyz(a.m_Limit.m_Max),
                length = a.m_Length,
                type = a.m_Type
            }).ToArray(),
            pose = pose.Select(Trs),
            bone_index = human.m_HumanBoneIndex,
            left_hand = left,
            right_hand = right,
            mass = human.m_HumanBoneMass,
            scale = human.m_Scale,
            arm_twist = human.m_ArmTwist,
            forearm_twist = human.m_ForeArmTwist,
            upperleg_twist = human.m_UpperLegTwist,
            leg_twist = human.m_LegTwist,
            has_tdof = human.m_HasTDoF,
            skeleton_index = constant.m_HumanSkeletonIndexArray,
            root = Trs(human.m_RootX)
        };
    }

    internal static object ExportClip(AnimationClip animation, string work, int id, bool? blendShapeClamp)
    {
        var clip = animation.m_MuscleClip.m_Clip;
        if (clip.m_ACLClip is not GIACLClip acl) throw new InvalidDataException();
        var (values, times) = acl.IsSet ? NativeAclDb.Decode(acl) : (Array.Empty<float>(), Array.Empty<float>());
        string file = $"samples-{id}.bin";
        using (var writer = new BinaryWriter(File.Create(Path.Combine(work, file))))
            foreach (float value in values) writer.Write(value);
        return new
        {
            name = animation.m_Name,
            start = animation.m_MuscleClip.m_StartTime,
            stop = animation.m_MuscleClip.m_StopTime,
            sample_rate = animation.m_SampleRate,
            mirror = animation.m_MuscleClip.m_Mirror,
            blend_shape_clamp = blendShapeClamp,
            file,
            times,
            acl_count = acl.IsSet ? acl.CurveCount : 0,
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

    internal static object ReadController(AnimatorController controller, SerializedFile file)
    {
        var constant = controller.m_Controller;
        var clips = controller.m_AnimationClips;
        if (clips.Count > MaxControllerClips) throw new InvalidDataException();
        string? Name(uint hash) => controller.m_TOS.TryGetValue(hash, out var value) ? value : null;
        string Cab(int fileId)
        {
            if (fileId == 0) return file.fileName;
            if (fileId < 1 || fileId > file.m_Externals.Count) throw new InvalidDataException();
            return file.m_Externals[fileId - 1].fileName;
        }
        var layers = constant.m_LayerArray.Select(layer => new
        {
            name = Name(layer.m_Binding),
            state_machine = layer.m_StateMachineIndex,
            motion_set = layer.m_StateMachineMotionSetIndex,
            blend_mode = layer.m_LayerBlendingMode,
            weight = layer.m_DefaultWeight,
            synced_affects_timing = layer.m_SyncedLayerAffectsTiming,
            skeleton_mask = (layer.m_SkeletonMask?.m_Data ?? []).Select(e => new { path_hash = e.m_PathHash, weight = e.m_Weight }).ToArray()
        }).ToArray();
        if (constant.m_StateMachineArray.Count == 0) throw new InvalidDataException();
        var machine = constant.m_StateMachineArray[0];
        var states = machine.m_StateConstantArray;
        if (states.Count > MaxStates) throw new InvalidDataException();
        int transitionCount = 0;
        object Condition(ConditionConstant c) => new { param = Name(c.m_EventID), mode = c.m_ConditionMode, threshold = c.m_EventThreshold };
        object[] Conditions(List<ConditionConstant> list)
        {
            if (list.Count > MaxConditions) throw new InvalidDataException();
            return list.Select(Condition).ToArray();
        }
        object Transition(TransitionConstant t)
        {
            if (++transitionCount > MaxTransitions) throw new InvalidDataException();
            return new
            {
                dest = t.m_DestinationState,
                duration = t.m_TransitionDuration,
                offset = t.m_TransitionOffset,
                exit_time = t.m_ExitTime,
                has_exit_time = t.m_HasExitTime,
                fixed_duration = t.m_HasFixedDuration,
                conditions = Conditions(t.m_ConditionConstantArray)
            };
        }
        object? Motion(StateConstant state, int set)
        {
            if (set >= state.m_BlendTreeConstantIndexArray.Length) return null;
            int tree = state.m_BlendTreeConstantIndexArray[set];
            if (tree < 0) return null;
            if (tree >= state.m_BlendTreeConstantArray.Count) throw new InvalidDataException();
            var nodes = state.m_BlendTreeConstantArray[tree].m_NodeArray;
            if (nodes.Count == 0) return null;
            var leaves = new List<object>();
            for (int index = 0; index < nodes.Count; index++)
            {
                var node = nodes[index];
                if (node.m_ChildIndices.Length != 0 || node.m_ClipID == uint.MaxValue) continue;
                if (node.m_ClipID >= clips.Count || leaves.Count >= MaxLeaves) throw new InvalidDataException();
                var pointer = clips[(int)node.m_ClipID];
                // A 1D parent supplies the leaf threshold; other blend types keep it unset.
                float? threshold = null;
                for (int parent = 0; parent < nodes.Count; parent++)
                {
                    int slot = Array.IndexOf(nodes[parent].m_ChildIndices, (uint)index);
                    if (slot >= 0 && nodes[parent].m_ChildThresholdArray is { } values && slot < values.Length)
                        threshold = values[slot];
                }
                leaves.Add(new { cab = Cab(pointer.m_FileID), path_id = pointer.m_PathID, node_index = index, threshold });
            }
            return new { blend_type = nodes[0].m_BlendType, leaves };
        }
        return new
        {
            name = controller.m_Name,
            layers,
            states = states.Select(state => new
            {
                name = Name(state.m_NameID),
                speed = state.m_Speed,
                mirror = state.m_Mirror,
                mirror_param = state.m_MirrorParamID,
                ik_on_feet = state.m_IKOnFeet,
                loop = state.m_Loop,
                tag = Name(state.m_TagID),
                path = Name(state.m_FullPathID),
                transitions = state.m_TransitionConstantArray.Select(Transition).ToArray(),
                motions = Enumerable.Range(0, layers.Length == 0 ? 0 : (int)layers.Max(l => l.motion_set) + 1).Select(set => Motion(state, set)).ToArray()
            }).ToArray(),
            default_state = machine.m_DefaultState,
            any_state = machine.m_AnyStateTransitionConstantArray.Select(Transition).ToArray(),
            selectors = machine.m_SelectorStateConstantArray.Select(s => new
            {
                entry = s.m_isEntry,
                transitions = s.m_TransitionConstantArray.Select(t =>
                {
                    if (++transitionCount > MaxTransitions) throw new InvalidDataException();
                    return (object)new { dest = t.m_Destination, conditions = Conditions(t.m_ConditionConstantArray) };
                }).ToArray()
            }).ToArray()
        };
    }
}

// ABI, alignment and the streamed-bulk rule follow AnimeStudio's MIT-licensed DBACL wrapper.
internal static class NativeAclDb
{
    [StructLayout(LayoutKind.Sequential)]
    private struct Samples { public nint Values; public int Count; public nint Times; public int Frames; }
    [DllImport("AnimeStudio.ACL.DB", CallingConvention = CallingConvention.Cdecl, EntryPoint = "DecompressTracks")]
    private static extern void Decode(nint data, nint database, nint streamer, ref Samples output);
    [DllImport("AnimeStudio.ACL.DB", CallingConvention = CallingConvention.Cdecl, EntryPoint = "Dispose")]
    private static extern void Dispose(ref Samples output);

    private static bool BulkOffset(byte[] database, out long offset)
    {
        offset = 0;
        if (database.Length < 64 || BitConverter.ToUInt32(database, 8) != 0xAC11DB01) return false;
        // Inline bulk data needs no separate streamer pointer.
        if ((BitConverter.ToUInt16(database, 14) & 1) != 0) return false;
        long chunks = (long)BitConverter.ToUInt32(database, 16) + BitConverter.ToUInt32(database, 20);
        long entries = BitConverter.ToUInt32(database, 28);
        offset = 64 + 8 * (chunks + entries);
        long medium = (BitConverter.ToUInt32(database, 40) + 3L) & ~3L;
        return offset + medium + BitConverter.ToUInt32(database, 44) <= database.Length;
    }

    internal static (float[], float[]) Decode(GIACLClip clip)
    {
        byte[][] data = [clip.m_ClipData, clip.m_DatabaseData];
        if (data.Any(d => d is null || d.Length == 0) || clip.CurveCount == 0 || clip.CurveCount > 1024 * 1024)
            throw new InvalidDataException();
        nint[] allocations = new nint[2], aligned = new nint[2];
        var output = new Samples();
        try
        {
            for (int i = 0; i < 2; i++)
            {
                // Over-allocate a full alignment step; aligning a +8 buffer could overrun it.
                allocations[i] = Marshal.AllocHGlobal(data[i].Length + 16);
                aligned[i] = (allocations[i] + 15) & ~15;
                Marshal.Copy(data[i], 0, aligned[i], data[i].Length);
            }
            nint streamer = BulkOffset(data[1], out long offset) ? aligned[1] + (nint)offset : 0;
            Decode(aligned[0], aligned[1], streamer, ref output);
            // The decoder reports failure only through empty or inconsistent output.
            if (output.Values == 0 || output.Times == 0 || output.Frames <= 0 || output.Frames > 100000
                || output.Count <= 0 || output.Count > 32 * 1024 * 1024 || output.Count != output.Frames * (long)clip.CurveCount)
                throw new InvalidDataException();
            var values = new float[output.Count]; var times = new float[output.Frames];
            Marshal.Copy(output.Values, values, 0, values.Length);
            Marshal.Copy(output.Times, times, 0, times.Length);
            if (!values.All(float.IsFinite) || !times.All(float.IsFinite)) throw new InvalidDataException();
            return (values, times);
        }
        finally
        {
            if (output.Values != 0 || output.Times != 0) Dispose(ref output);
            foreach (var allocation in allocations) Marshal.FreeHGlobal(allocation);
        }
    }
}
