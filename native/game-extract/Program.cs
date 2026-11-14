using CUE4Parse;
using CUE4Parse.Compression;
using CUE4Parse.Encryption.Aes;
using CUE4Parse.FileProvider;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using CUE4Parse.Utils;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Options;
using CUE4Parse_Conversion.Exporters;
using CUE4Parse.UE4.Assets.Exports.Engine;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace ModViewer.GameExtract;

internal static class Program
{
    // No general CLI, hosting framework or MCP surface: one private extraction job.
    public static async Task<int> Main(string[] args)
    {
        if (args is ["--self-test"])
        {
            Contract.Verify();
            if (!CUE4ParseNatives.IsFeatureAvailable("ACL\0"u8)) throw new InvalidOperationException("ACL decoder is missing.");
            Console.WriteLine("Extraction contract checks passed.");
            return 0;
        }
        if (args is not ["--paks", var paks, "--work", var work]) return 2;
        work = Path.GetFullPath(work);
        Globals.FatalObjectSerializationErrors = true;
        // Upstream diagnostics can contain private game information. Never publish them.
        Console.SetOut(TextWriter.Null);
        Console.SetError(TextWriter.Null);
        using var session = new GameSession(paks, work);
        string? line;
        while ((line = await Console.In.ReadLineAsync()) is not null)
        {
            if (line.Length > 256 * 1024) return 2;
            var request = JObject.Parse(line);
            var id = (int?)request["id"] ?? 0;
            if (id < 1) return 2;
            object response;
            try { response = await session.Execute(request); }
            catch { response = new { error = "Game extraction failed." }; }
            var target = Path.Combine(work, $"command-{id}.json");
            await File.WriteAllTextAsync(target + ".tmp", JsonConvert.SerializeObject(response));
            File.Move(target + ".tmp", target);
        }
        return 0;
    }
}

internal sealed class GameSession(string paks, string work) : IDisposable
{
    private DefaultFileProvider? provider;
    private string[] packages = [];
    private readonly Dictionary<string, string[]> types = new(StringComparer.OrdinalIgnoreCase);

    private async Task Mount()
    {
        if (provider is not null) return;
        // Explicit existing paths prevent the upstream helpers' download fallback.
        foreach (var name in new[] { "oodle-data-shared.dll", "zlib-ng2.dll", "CUE4Parse-Natives.dll" })
            if (!File.Exists(Path.Combine(AppContext.BaseDirectory, name))) throw new FileNotFoundException();
        await OodleHelper.InitializeAsync(Path.Combine(AppContext.BaseDirectory, "oodle-data-shared.dll"));
        await ZlibHelper.InitializeAsync(Path.Combine(AppContext.BaseDirectory, "zlib-ng2.dll"));
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
        // Only data from the established public key service; keys never leave memory.
        using var response = await http.GetAsync("https://yarik0chka.github.io/wuwa-keys/keys.json", HttpCompletionOption.ResponseHeadersRead);
        response.EnsureSuccessStatusCode();
        using var stream = await response.Content.ReadAsStreamAsync();
        using var keyReader = new StreamReader(stream);
        var text = new char[1024 * 1024 + 1];
        var length = await keyReader.ReadBlockAsync(text);
        if (length == text.Length) throw new InvalidDataException();
        var keys = JObject.Parse(new string(text, 0, length));
        var main = new FAesKey((string?)keys["mainKey"] ?? throw new InvalidDataException());
        var keyPairs = new List<KeyValuePair<FGuid, FAesKey>> { new(default, main) };
        foreach (var key in keys["dynamicKeys"] ?? new JArray())
            keyPairs.Add(new(new FGuid((string?)key["guid"] ?? ""), new FAesKey((string?)key["key"] ?? "")));
        var mounted = new DefaultFileProvider(paks, SearchOption.AllDirectories,
            new VersionContainer(EGame.GAME_WutheringWaves), StringComparer.OrdinalIgnoreCase);
        try
        {
            mounted.Initialize();
            await mounted.SubmitKeysAsync(keyPairs);
            await mounted.MountAsync();
            foreach (var reader in mounted.MountedVfs.Concat(mounted.UnloadedVfs))
                if (reader.AesKey is null) reader.AesKey = main;
            if (mounted.UnloadedVfs.Count > 0) await mounted.MountAsync();
            mounted.PostMount();
            mounted.LoadVirtualPaths();
            packages = mounted.Files.Values.Where(file => file.IsUePackage)
                .Select(file => file.PathWithoutExtension).Where(path => path.StartsWith("Client/Content/", StringComparison.OrdinalIgnoreCase))
                .Distinct(StringComparer.OrdinalIgnoreCase).Order(StringComparer.OrdinalIgnoreCase).ToArray();
            provider = mounted;
        }
        catch { mounted.Dispose(); throw; }
    }

    public async Task<object> Execute(JObject request)
    {
        var operation = (string?)request["op"];
        if (operation is not ("search" or "folders" or "export" or "batch")) throw new InvalidDataException();
        await Mount();
        if (operation == "folders")
        {
            var prefix = Contract.Package((string?)request["folder"]) + "/";
            return new
            {
                folders = packages.Where(path => path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                .Select(path => path[prefix.Length..].Split('/')).Where(parts => parts.Length > 1)
                .Select(parts => parts[0]).Distinct(StringComparer.OrdinalIgnoreCase).Order(StringComparer.OrdinalIgnoreCase).ToArray()
            };
        }
        if (operation == "search") return Search(request);
        var kind = (string?)request["kind"];
        if (kind is not ("json" or "mesh" or "anim")) throw new InvalidDataException();
        var destination = Contract.Destination(work, (string?)request["outDir"]);
        var paths = operation == "batch" ? request["paths"]?.ToObject<string[]>() : [(string?)request["path"] ?? ""];
        if (paths is null || paths.Length is < 1 or > 500) throw new InvalidDataException();
        foreach (var path in paths) Contract.Package(path);
        if (operation == "export") return new { rootFiles = await Export(kind, paths[0], destination) };
        var items = new List<object>();
        foreach (var path in paths)
        {
            try { items.Add(new { path, state = "completed", rootFiles = await Export(kind, path, destination), failures = Array.Empty<string>() }); }
            catch { items.Add(new { path, state = "failed", rootFiles = Array.Empty<string>(), failures = new[] { "Export failed." } }); }
        }
        return new { items };
    }

    private object Search(JObject request)
    {
        var kind = (string?)request["type"];
        if (kind is not ("SkeletalMesh" or "AnimSequence")) throw new InvalidDataException();
        var prefix = Contract.Package((string?)request["under"]) + "/";
        var terms = ((string?)request["query"] ?? "").Split(' ', StringSplitOptions.RemoveEmptyEntries);
        var candidates = packages.Where(path => path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)
            && terms.All(term => path.Contains(term, StringComparison.OrdinalIgnoreCase)));
        var hits = new List<object>();
        var scanned = 0;
        foreach (var path in candidates)
        {
            if (++scanned > 20000 || hits.Count == 501) return new { truncated = true, results = hits.Take(500) };
            if (!types.TryGetValue(path, out var exports))
            {
                try
                {
                    var package = Load(path);
                    exports = Enumerable.Range(1, package.ExportMapLength)
                        .Select(index => package.ResolvePackageIndex(new FPackageIndex(package, index))?.Class?.Name.Text ?? "").ToArray();
                }
                catch { exports = []; }
                types.Add(path, exports);
            }
            if (exports.Contains(kind)) hits.Add(new { path });
        }
        return new { truncated = hits.Count > 500, results = hits.Take(500) };
    }

    private IPackage Load(string path)
    {
        Contract.Package(path);
        foreach (var extension in new[] { ".uasset", ".umap" })
            if (provider!.TryGetGameFile(path + extension, out var file)) return provider.LoadPackage(file);
        throw new FileNotFoundException();
    }

    private async Task<string[]> Export(string kind, string path, string destination)
    {
        var package = Load(path);
        if (kind == "json")
        {
            var target = Contract.Destination(destination, Path.Combine(destination, path + ".json"));
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            using var stream = File.CreateText(target);
            using var writer = new JsonTextWriter(stream);
            var serializer = JsonSerializer.CreateDefault();
            writer.WriteStartArray();
            foreach (var export in package.ExportsLazy) serializer.Serialize(writer, export.Value);
            writer.WriteEndArray();
            return [target];
        }
        var roots = package.ExportsLazy.Select(export => export.Value)
            .Where(export => kind == "mesh" ? export is USkeletalMesh : export is UAnimSequence).ToArray();
        if (roots.Length != 1) throw new InvalidDataException();
        Contract.Segment(roots[0].Name);
        ExporterBase exporter = kind == "mesh" ? new SkinnedAssetExporter((USkinnedAsset)roots[0]) : new AnimationExporter((UAnimSequence)roots[0]);
        if (!Contract.Package(exporter.PackagePath).Equals(path, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException();
        var session = new ExportSession { MaxDegreeOfParallelism = 1 };
        session.Add(exporter);
        var options = new ExportOptions(meshFormat: kind == "mesh" ? EMeshFormat.UEFormat : EMeshFormat.ActorX,
            exportMaterials: false, exportMorphTargets: false);
        var results = await session.RunAsync(destination, options);
        if (results.Count == 0 || results.Any(result => !result.Success)) throw new InvalidDataException();
        return results.SelectMany(result => result.DiskFilePaths ?? []).Select(file => Contract.Destination(destination, file)).ToArray();
    }

    public void Dispose() => provider?.Dispose();
}

internal static class Contract
{
    public static string Segment(string value)
    {
        if (value.Length is < 1 or > 256 || value is "." or ".." || value.Any(c => !char.IsAsciiLetterOrDigit(c) && c is not ('_' or '-')))
            throw new InvalidDataException();
        return value;
    }

    public static string Package(string? value)
    {
        if (value is null || value.Length > 1024 || !value.StartsWith("Client/Content/", StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException();
        foreach (var segment in value.Split('/')) Segment(segment);
        return value;
    }

    public static string Destination(string root, string? value)
    {
        if (value is null) throw new InvalidDataException();
        var path = Path.GetFullPath(value);
        if (!path.StartsWith(Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException();
        return path;
    }

    public static void Verify()
    {
        Package("Client/Content/Character/Body_01");
        foreach (var path in new[] { "../outside", "Client/Content/../outside", "Client/Content/a\\b", "Client/Content//a", "Client/Content/a:stream" })
        {
            try { Package(path); } catch (InvalidDataException) { continue; }
            throw new InvalidOperationException("Unsafe package accepted.");
        }
        var root = Path.Combine(Path.GetTempPath(), "extract-contract");
        Destination(root, Path.Combine(root, "output", "mesh.json"));
        foreach (var path in new[] { root + "-sibling", Path.Combine(root, "..", "outside") })
        {
            try { Destination(root, path); } catch (InvalidDataException) { continue; }
            throw new InvalidOperationException("Output escaped staging.");
        }
    }
}
