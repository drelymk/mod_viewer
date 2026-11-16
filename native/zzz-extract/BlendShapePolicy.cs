using AnimeStudio;

namespace ModViewer.ZzzExtract;

internal static class BlendShapePolicy
{
    internal static bool? Read(string root, Game game)
    {
        string path = Path.Combine(root, "ZenlessZoneZero_Data", "globalgamemanagers");
        if (!File.Exists(path) || new FileInfo(path).Length > 16 * 1024 * 1024) return null;
        Program.ValidateFile(root, path);
        var manager = new AssetsManager { Game = game, Silent = true, SkipProcess = true, ResolveDependencies = false };
        try
        {
            manager.LoadFiles([path], mergeSplitAssets: false);
            bool? result = null;
            int count = 0;
            foreach (var file in manager.assetsFileList)
                foreach (var info in file.m_Objects)
                    if ((ClassIDType)info.classID == ClassIDType.PlayerSettings)
                    {
                        if (++count != 1 || info.byteSize > 1024 * 1024) return null;
                        var reader = new ObjectReader(file.reader, file, info, game);
                        var settings = new AnimeStudio.Object(reader).ToType();
                        if (settings?["legacyClampBlendShapeWeights"] is bool clamp) result = clamp;
                    }
            return result;
        }
        finally { manager.Clear(); }
    }
}
