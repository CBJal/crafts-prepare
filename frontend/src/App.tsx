import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { nextChecked } from "./selection";
import type { Capabilities, Clip, Job } from "./types";

const FLAGS = ["", "OK", "KEEP", "NG"] as const;

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function frameLabel(seconds: number, rate: string | null): string {
  if (!rate || !rate.includes("/")) return seconds.toFixed(3) + " s";
  const [num, den] = rate.split("/").map(Number);
  if (!num || !den) return seconds.toFixed(3) + " s";
  const frame = Math.max(0, Math.round((seconds * num) / den));
  const nominal = Math.max(1, Math.round(num / den));
  const ff = frame % nominal;
  const whole = Math.floor(frame / nominal);
  const ss = whole % 60;
  const mm = Math.floor(whole / 60) % 60;
  const hh = Math.floor(whole / 3600);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(hh)}:${pad(mm)}:${pad(ss)}:${pad(ff)}`;
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

type ThemeChoice = "light" | "dark" | "system";
const THEME_KEY = "crafts-prepare-theme";

function readTheme(): ThemeChoice {
  const stored = window.localStorage.getItem(THEME_KEY);
  return stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
}

function applyTheme(choice: ThemeChoice) {
  const dark = choice === "dark" || (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.themeChoice = choice;
}

export function App() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importPath, setImportPath] = useState("");
  const [projectPath, setProjectPath] = useState("");
  const [projectName, setProjectName] = useState("Untitled");
  const [notice, setNotice] = useState("Local only. Sources stay where they are.");
  const [busy, setBusy] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [destination, setDestination] = useState("");
  const [suffix, setSuffix] = useState("_cut");
  const [preset, setPreset] = useState("h264_mp4");
  const [audio, setAudio] = useState(true);
  const [ack, setAck] = useState(false);
  const [exposure, setExposure] = useState(0);
  const [saturation, setSaturation] = useState(1);
  const [fit, setFit] = useState("none");
  const [width, setWidth] = useState(1280);
  const [height, setHeight] = useState(720);
  const [rotate, setRotate] = useState("0");
  const [stabilization, setStabilization] = useState("off");
  const [smoothing, setSmoothing] = useState(10);
  const [shakiness, setShakiness] = useState(5);
  const [accuracy, setAccuracy] = useState(15);
  const [cropMode, setCropMode] = useState("black");
  const [zoom, setZoom] = useState(0);
  const [optzoom, setOptzoom] = useState(0);
  const [deshakeRx, setDeshakeRx] = useState(16);
  const [deshakeRy, setDeshakeRy] = useState(16);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewKind, setPreviewKind] = useState<"source" | "proxy" | "still">("still");
  const [previewNote, setPreviewNote] = useState("");
  const [lutPath, setLutPath] = useState("");
  const [gyroLog, setGyroLog] = useState("");
  const [gyroProject, setGyroProject] = useState("");
  const [gyroPreset, setGyroPreset] = useState("");
  const [gyroOutput, setGyroOutput] = useState("");
  const [gcsvText, setGcsvText] = useState("");
  const [telemetryText, setTelemetryText] = useState("");
  const [ffmpegPath, setFfmpegPath] = useState("ffmpeg");
  const [ffprobePath, setFfprobePath] = useState("ffprobe");
  const [gyroPath, setGyroPath] = useState("gyroflow");
  const [telemetryPath, setTelemetryPath] = useState("");
  const [checked, setChecked] = useState<string[]>([]);
  const [batchOp, setBatchOp] = useState<"export" | "gyro">("export");
  const [gyroFormat, setGyroFormat] = useState<"h264_mp4" | "prores_422">("h264_mp4");
  const [gyroSourceSize, setGyroSourceSize] = useState(true);
  const [gyroWidth, setGyroWidth] = useState(1920);
  const [gyroHeight, setGyroHeight] = useState(1080);
  const [gyroAudio, setGyroAudio] = useState(true);
  const [batchPreset, setBatchPreset] = useState("");
  const [batchProblems, setBatchProblems] = useState<{ clip_id: string | null; code: string; message: string }[]>([]);
  const [theme, setTheme] = useState<ThemeChoice>(readTheme);
  const libraryPath = useRef<string | undefined>(undefined);

  const selected = clips.find((clip) => clip.id === selectedId) ?? null;
  const checkedClips = clips.filter((clip) => checked.includes(clip.id));

  async function refresh() {
    const [nextCaps, project, queue] = await Promise.all([api.capabilities(), api.project(), api.jobs()]);
    setCaps(nextCaps);
    setClips(project.clips);
    setJobs(queue.jobs);
    setProjectName(project.name);
    const nextPath = project.path ?? "";
    const previousPath = libraryPath.current;
    libraryPath.current = nextPath;
    if (project.path) setProjectPath(project.path);
    setChecked((current) => nextChecked(current, previousPath, nextPath, project.clips.map((clip) => clip.id)));
    setSelectedId((current) => (current && project.clips.some((clip) => clip.id === current) ? current : project.clips[0]?.id ?? null));
  }

  useEffect(() => {
    refresh().catch((error: Error) => setNotice(error.message));
  }, []);

  useEffect(() => {
    applyTheme(theme);
    window.localStorage.setItem(THEME_KEY, theme);
    if (theme !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [theme]);

  useEffect(() => {
    if (!jobs.some((job) => job.status === "queued" || job.status === "running")) return;
    const timer = window.setInterval(() => {
      api.jobs().then((payload) => setJobs(payload.jobs)).catch(() => undefined);
    }, 800);
    return () => window.clearInterval(timer);
  }, [jobs]);

  useEffect(() => {
    if (!selected) {
      setPreviewUrl(null);
      setPreviewKind("still");
      return;
    }
    if (selected.browser_playable) {
      setPreviewKind("source");
      setPreviewUrl(`/api/media/${selected.id}/file`);
      setPreviewNote("This player shows the source file. Exposure, saturation, and LUT stills use the same FFmpeg filters as export. Vidstab and deshake run only in the export.");
      return;
    }
    setPreviewKind("still");
    setPreviewUrl(`/api/media/${selected.id}/thumbnail?t=0`);
    setPreviewNote("This container is not played directly. The image is one frame. Create a viewing proxy for at most 60 seconds of motion playback.");
  }, [selected?.id, selected?.browser_playable]);

  const stillUrl = useMemo(() => {
    if (!selected) return "";
    const params = new URLSearchParams({
      t: String(selected.trim_in || 0),
      exposure: String(exposure),
      saturation: String(saturation),
      rotate,
      fit,
    });
    if (fit !== "none") {
      params.set("width", String(width));
      params.set("height", String(height));
    }
    if (lutPath.trim()) params.set("lut", lutPath.trim());
    return `/api/media/${selected.id}/thumbnail?${params.toString()}`;
  }, [selected, exposure, saturation, rotate, fit, width, height, lutPath]);

  async function onImport() {
    setBusy(true);
    try {
      const result = await api.importMedia(importPath);
      const names = result.added.map((clip) => clip.name).join(", ");
      const skipped = result.skipped.length ? ` Skipped ${result.skipped.length}.` : "";
      const dupes = result.duplicates.length ? ` ${result.duplicates.length} duplicate.` : "";
      setNotice(names ? `Imported ${names}.${skipped}${dupes}` : `Nothing new.${skipped}${dupes}`);
      await refresh();
      if (result.added[0]) setSelectedId(result.added[0].id);
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onSaveFlag(flag: Clip["flag"]) {
    if (!selected) return;
    const updated = await api.patchClip(selected.id, { flag });
    setClips((current) => current.map((clip) => (clip.id === updated.id ? updated : clip)));
  }

  async function onNotes(notes: string) {
    if (!selected) return;
    const updated = await api.patchClip(selected.id, { notes });
    setClips((current) => current.map((clip) => (clip.id === updated.id ? updated : clip)));
  }

  async function onTrim(trimIn: number, trimOut: number) {
    if (!selected) return;
    try {
      const updated = await api.patchClip(selected.id, { trim_in: trimIn, trim_out: trimOut });
      setClips((current) => current.map((clip) => (clip.id === updated.id ? updated : clip)));
      setNotice("Trim stored in the project. The source file was not cut.");
    } catch (error) {
      setNotice((error as Error).message);
    }
  }

  async function onExport() {
    if (!selected) return;
    setBusy(true);
    try {
      const job = await api.exportJob({
        clip_id: selected.id,
        preset_id: preset,
        in_seconds: selected.trim_in,
        out_seconds: selected.trim_out,
        destination_dir: destination,
        suffix,
        audio,
        acknowledge_bit_depth_conversion: ack,
        exposure,
        saturation,
        rotate,
        fit,
        width: fit === "none" ? null : width,
        height: fit === "none" ? null : height,
        stabilization,
        use_vidstab_auto: false,
        stab_smoothing: smoothing,
        stab_shakiness: shakiness,
        stab_accuracy: accuracy,
        stab_crop: cropMode,
        stab_zoom: zoom,
        stab_optzoom: optzoom,
        deshake_rx: deshakeRx,
        deshake_ry: deshakeRy,
        lut_path: lutPath.trim() || null,
      });
      setNotice(`Queued ${job.request.filename ?? "export"}.`);
      await refresh();
    } catch (error) {
      const coded = error as Error & { code?: string };
      if (coded.code === "bit_depth") setAck(false);
      setNotice(coded.message);
    } finally {
      setBusy(false);
    }
  }

  async function onBatch() {
    if (!destination || checkedClips.length === 0) return;
    setBusy(true);
    setBatchProblems([]);
    const sharedExport = {
      preset_id: preset,
      audio,
      acknowledge_bit_depth_conversion: ack,
      exposure,
      saturation,
      rotate,
      fit,
      width: fit === "none" ? null : width,
      height: fit === "none" ? null : height,
      stabilization,
      use_vidstab_auto: false,
      stab_smoothing: smoothing,
      stab_shakiness: shakiness,
      stab_accuracy: accuracy,
      stab_crop: cropMode,
      stab_zoom: zoom,
      stab_optzoom: optzoom,
      deshake_rx: deshakeRx,
      deshake_ry: deshakeRy,
      lut_path: lutPath.trim() || null,
    };
    const body =
      batchOp === "export"
        ? {
            operation: "export",
            clip_ids: checkedClips.map((clip) => clip.id),
            destination_dir: destination,
            suffix,
            export: sharedExport,
          }
        : {
            operation: "gyro",
            clip_ids: checkedClips.map((clip) => clip.id),
            destination_dir: destination,
            suffix,
            gyro: {
              format: gyroFormat,
              audio: gyroAudio,
              width: gyroSourceSize ? null : gyroWidth,
              height: gyroSourceSize ? null : gyroHeight,
              preset_path: batchPreset.trim() || null,
            },
          };
    try {
      const result = await api.batch(body);
      setNotice(`Queued ${result.count} ${batchOp === "gyro" ? "gyro" : "export"} job${result.count === 1 ? "" : "s"}.`);
      await refresh();
    } catch (error) {
      const coded = error as Error & { code?: string; problems?: { clip_id: string | null; code: string; message: string }[] };
      setBatchProblems(coded.problems ?? []);
      setNotice(coded.problems?.length ? "Nothing was queued." : coded.message);
    } finally {
      setBusy(false);
    }
  }

  function applyAuto() {
    if (!caps) return;
    setStabilization("vidstab");
    setShakiness(caps.vidstab_auto.shakiness);
    setAccuracy(caps.vidstab_auto.accuracy);
    setSmoothing(caps.vidstab_auto.smoothing);
    setCropMode(caps.vidstab_auto.crop);
    setZoom(caps.vidstab_auto.zoom);
    setOptzoom(caps.vidstab_auto.optzoom);
    setNotice("Auto filled the vidstab numbers. They are sent to vidstabdetect and vidstabtransform.");
  }

  const probe = asRecord(selected?.probe);
  const videoList = Array.isArray(probe?.video) ? probe.video.map(asRecord).filter((item) => item !== null) : [];
  const video = videoList[0];
  const dataStreams = Array.isArray(probe?.data) ? probe.data.map(asRecord).filter((item) => item !== null) : [];
  const audioCodecs = Array.isArray(selected?.summary?.audio_codecs)
    ? selected.summary.audio_codecs.filter((item): item is string => typeof item === "string")
    : [];
  const activePreset = caps?.presets.find((item) => item.id === preset);

  return (
    <div className="shell">
      <header className="top">
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <div>
            <p className="eyebrow">Local bench</p>
            <h1>Crafts Prepare</h1>
          </div>
        </div>
        <label className="theme-choice">
          Theme
          <select aria-label="Color theme" value={theme} onChange={(event) => setTheme(event.target.value as ThemeChoice)}>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
            <option value="system">System</option>
          </select>
        </label>
        <label className="project-name">
          Project
          <input value={projectName} onChange={(event) => setProjectName(event.target.value)} onBlur={() => api.rename(projectName).catch((error: Error) => setNotice(error.message))} />
        </label>
        <div className="chips">
          <Chip ok={!!caps?.ffmpeg.available} label="FFmpeg" />
          <Chip ok={!!caps?.ffprobe.available} label="ffprobe" />
          <Chip ok={!!caps?.gyroflow.available} label="Gyroflow" />
          <Chip ok={!!caps?.ffmpeg.filters?.vidstabdetect} label="vidstab" />
        </div>
        <button className="ghost" onClick={() => setShowSettings((value) => !value)}>
          Capabilities
        </button>
      </header>

      <p className="notice">{notice}</p>

      <div className="workspace">
        <section className="library">
          <header>
            <h2>Library</h2>
            <p>Path import. Folder symlinks that leave the folder are skipped.</p>
          </header>
          <div className="import-row">
            <input
              aria-label="Media file or folder path"
              placeholder="File or folder path"
              value={importPath}
              onChange={(event) => setImportPath(event.target.value)}
            />
            <button onClick={onImport} disabled={busy || !importPath}>
              Import
            </button>
          </div>
          <div className="select-row">
            <button type="button" className="ghost" onClick={() => setChecked(clips.map((clip) => clip.id))} disabled={!clips.length}>
              Select all
            </button>
            <button type="button" className="ghost" onClick={() => setChecked([])} disabled={!checked.length}>
              Clear selection
            </button>
            <span>{checkedClips.length} selected</span>
          </div>
          <ul>
            {clips.length === 0 && <li className="empty">No clips yet.</li>}
            {clips.map((clip) => (
              <li key={clip.id} className="clip-row">
                <label className="clip-check">
                  <input
                    type="checkbox"
                    aria-label={`Select ${clip.name} for batch`}
                    checked={checked.includes(clip.id)}
                    onChange={(event) =>
                      setChecked((current) =>
                        event.target.checked ? [...current, clip.id] : current.filter((id) => id !== clip.id),
                      )
                    }
                  />
                </label>
                <button
                  className={`clip${clip.id === selectedId ? " active" : ""}${checked.includes(clip.id) ? " checked" : ""}`}
                  onClick={() => setSelectedId(clip.id)}
                >
                  <strong>{clip.name}</strong>
                  <span>
                    {clip.summary.codec} · {clip.summary.width}×{clip.summary.height} · {clip.summary.frame_rate} · {clip.summary.bit_depth ?? "?"}
                    -bit
                  </span>
                  {clip.flag && <em className={`flag flag-${clip.flag}`}>{clip.flag}</em>}
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section className="viewer">
          <header>
            <h2>Preview</h2>
            <p>{previewNote}</p>
            {selected && !selected.browser_playable && (
              <button
                className="ghost"
                onClick={() =>
                  api
                    .proxy(selected.id)
                    .then((proxy) => {
                      if (proxy.kind === "proxy") {
                        setPreviewKind("proxy");
                        setPreviewUrl(proxy.url);
                        const limit = proxy.duration_limit_seconds ?? 60;
                        setPreviewNote(`${proxy.note} Playback here is capped at ${limit} seconds.`);
                      } else {
                        setPreviewKind("source");
                        setPreviewUrl(proxy.url);
                        setPreviewNote(proxy.note);
                      }
                    })
                    .catch((error: Error) => setNotice(error.message))
                }
              >
                Create viewing proxy
              </button>
            )}
          </header>
          {selected && previewUrl && (previewKind === "source" || previewKind === "proxy") && (
            <video key={previewUrl} src={previewUrl} controls preload="metadata" />
          )}
          {selected && previewKind === "still" && previewUrl && <img src={previewUrl} alt="Frame from the selected clip" />}
          {!selected && <div className="stage-empty">Import a clip to preview it.</div>}
          {selected && (
            <div className="trim">
              <label>
                In seconds
                <input
                  type="number"
                  min={0}
                  step="0.04"
                  value={selected.trim_in}
                  onChange={(event) => onTrim(Number(event.target.value), selected.trim_out)}
                />
              </label>
              <label>
                Out seconds
                <input
                  type="number"
                  min={0}
                  step="0.04"
                  value={selected.trim_out}
                  onChange={(event) => onTrim(selected.trim_in, Number(event.target.value))}
                />
              </label>
              <p>
                {frameLabel(selected.trim_in, selected.summary.frame_rate)} → {frameLabel(selected.trim_out, selected.summary.frame_rate)}
                <span> Frame display from the rational rate {selected.summary.frame_rate}. Seconds are what the export uses.</span>
              </p>
            </div>
          )}
          {selected && (
            <figure className="still">
              <img src={stillUrl} alt="Adjustment still from the same filter graph used for export" />
              <figcaption>Adjustment still. Temporal stabilization is not baked into this frame.</figcaption>
            </figure>
          )}
        </section>

        <section className="inspector">
          <header>
            <h2>Inspector</h2>
          </header>
          {selected && video ? (
            <dl>
              <dt>Path</dt>
              <dd>{selected.path}</dd>
              <dt>SHA-256</dt>
              <dd className="mono">{selected.sha256}</dd>
              <dt>Size</dt>
              <dd>{formatBytes(selected.size)}</dd>
              <dt>Codec</dt>
              <dd>
                {asText(video.codec) || "unknown"} {asText(video.profile) ? `· ${asText(video.profile)}` : ""}
              </dd>
              <dt>Frame</dt>
              <dd>
                {typeof video.width === "number" ? video.width : "?"}×{typeof video.height === "number" ? video.height : "?"} · {asText(video.pix_fmt) || "unknown"} ·{" "}
                {typeof video.bit_depth === "number" ? video.bit_depth : "unknown"}-bit · {asText(video.frame_rate) || "unknown"}
              </dd>
              <dt>Duration</dt>
              <dd>{selected.summary.duration ?? "unknown"} s</dd>
              <dt>Timecode tag</dt>
              <dd>{selected.probe.timecode ?? "none in the probed tags"}</dd>
              <dt>Color</dt>
              <dd>
                {[video.color_primaries, video.color_transfer, video.color_space, video.color_range].map(asText).filter(Boolean).join(" · ") || "no color tags"}
              </dd>
              <dt>Audio</dt>
              <dd>{audioCodecs.join(", ") || "none"}</dd>
              <dt>Data streams</dt>
              <dd>
                {dataStreams.length === 0
                  ? "ffprobe listed none. That is not a claim that gyro metadata is absent."
                  : dataStreams.map((stream) => asText(stream.codec) || asText(stream.codec_tag) || "data").join(", ")}
              </dd>
            </dl>
          ) : (
            <p className="empty">Select a clip.</p>
          )}
          {selected && (
            <div className="flags">
              {FLAGS.map((flag) => (
                <button key={flag || "none"} className={selected.flag === flag ? "on" : ""} onClick={() => onSaveFlag(flag)}>
                  {flag || "Clear"}
                </button>
              ))}
            </div>
          )}
          {selected && (
            <label className="notes">
              Notes
              <textarea value={selected.notes} onChange={(event) => onNotes(event.target.value)} rows={3} />
            </label>
          )}

          <h2>Adjust</h2>
          <div className="grid">
            <label>
              Exposure
              <input type="range" min={-1} max={1} step={0.05} value={exposure} onChange={(event) => setExposure(Number(event.target.value))} />
              <span>{exposure.toFixed(2)}</span>
            </label>
            <label>
              Saturation
              <input type="range" min={0} max={3} step={0.05} value={saturation} onChange={(event) => setSaturation(Number(event.target.value))} />
              <span>{saturation.toFixed(2)}</span>
            </label>
            <label>
              Rotate
              <select value={rotate} onChange={(event) => setRotate(event.target.value)}>
                <option value="0">0°</option>
                <option value="90">90° clockwise</option>
                <option value="180">180°</option>
                <option value="270">270° clockwise</option>
              </select>
            </label>
            <label>
              .cube LUT path
              <input aria-label="CUBE LUT path" value={lutPath} placeholder="Optional .cube file" onChange={(event) => setLutPath(event.target.value)} />
            </label>
            <label>
              Fit
              <select value={fit} onChange={(event) => setFit(event.target.value)}>
                <option value="none">Original size</option>
                <option value="resize">Resize</option>
                <option value="crop">Crop to fill</option>
                <option value="letterbox">Letterbox</option>
              </select>
            </label>
            {fit !== "none" && (
              <>
                <label>
                  Width
                  <input type="number" value={width} min={2} step={2} onChange={(event) => setWidth(Number(event.target.value))} />
                </label>
                <label>
                  Height
                  <input type="number" value={height} min={2} step={2} onChange={(event) => setHeight(Number(event.target.value))} />
                </label>
              </>
            )}
          </div>
          <fieldset>
            <legend>Visual stabilization</legend>
            <p>These controls map to FFmpeg filters. They are not gyro stabilization.</p>
            <label>
              Mode
              <select value={stabilization} onChange={(event) => setStabilization(event.target.value)}>
                <option value="off">Off</option>
                <option value="vidstab">vidstab two-pass</option>
                <option value="deshake">deshake fallback</option>
              </select>
            </label>
            <button type="button" className="ghost" onClick={applyAuto}>
              Fill vidstab auto values
            </button>
            {stabilization === "vidstab" && (
              <div className="grid">
                <NumberField label="Shakiness" value={shakiness} min={1} max={10} onChange={setShakiness} />
                <NumberField label="Accuracy" value={accuracy} min={1} max={15} onChange={setAccuracy} />
                <NumberField label="Smoothing" value={smoothing} min={0} max={1000} onChange={setSmoothing} />
                <NumberField label="Zoom" value={zoom} min={-100} max={100} onChange={setZoom} />
                <label>
                  Crop
                  <select value={cropMode} onChange={(event) => setCropMode(event.target.value)}>
                    <option value="black">black</option>
                    <option value="keep">keep</option>
                  </select>
                </label>
                <label>
                  Optzoom
                  <select value={optzoom} onChange={(event) => setOptzoom(Number(event.target.value))}>
                    <option value={0}>0 off</option>
                    <option value={1}>1 static</option>
                    <option value={2}>2 adaptive</option>
                  </select>
                </label>
              </div>
            )}
            {stabilization === "deshake" && (
              <div className="grid">
                <NumberField label="deshake rx" value={deshakeRx} min={0} max={64} onChange={setDeshakeRx} />
                <NumberField label="deshake ry" value={deshakeRy} min={0} max={64} onChange={setDeshakeRy} />
              </div>
            )}
          </fieldset>
        </section>
      </div>

      <section className="queue">
        <header>
          <h2>Export queue</h2>
          <p>Sequential. Finished files are published only after ffprobe. Existing outputs and sources are not overwritten.</p>
        </header>
        <div className="export-row">
          <label>
            Destination folder
            <input aria-label="Export destination folder" value={destination} onChange={(event) => setDestination(event.target.value)} placeholder="Folder path" />
          </label>
          <label>
            Suffix
            <input aria-label="Filename suffix" value={suffix} onChange={(event) => setSuffix(event.target.value)} />
          </label>
          <label>
            Preset
            <select value={preset} onChange={(event) => setPreset(event.target.value)}>
              {caps?.presets.map((item) => (
                <option key={item.id} value={item.id} disabled={!item.available}>
                  {item.label} · {item.bit_depth}-bit {item.available ? "" : "(unavailable)"}
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={audio} onChange={(event) => setAudio(event.target.checked)} /> Audio
          </label>
          <label className="check">
            <input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} /> Acknowledge bit-depth conversion
          </label>
          <button onClick={onExport} disabled={busy || !selected || !destination}>
            Queue export
          </button>
        </div>
        {activePreset && <p className="preset-note">{activePreset.reason || `${activePreset.label} declares ${activePreset.pix_fmt}, ${activePreset.bit_depth}-bit.`}</p>}
        <section className="batch-panel">
          <header>
            <h2>Batch</h2>
            <p>
              {checkedClips.length} clip{checkedClips.length === 1 ? "" : "s"} will be queued. The focused preview clip is separate from the checked clips.
            </p>
          </header>
          <div className="export-row">
            <label>
              Operation
              <select aria-label="Batch operation" value={batchOp} onChange={(event) => setBatchOp(event.target.value as "export" | "gyro")}>
                <option value="export">Export</option>
                <option value="gyro">Gyro stabilization</option>
              </select>
            </label>
            {batchOp === "gyro" && (
              <>
                <label>
                  Gyro output
                  <select aria-label="Gyro output format" value={gyroFormat} onChange={(event) => setGyroFormat(event.target.value as "h264_mp4" | "prores_422")}>
                    <option value="h264_mp4">H.264 MP4 8-bit</option>
                    <option value="prores_422">ProRes 422 MOV 10-bit</option>
                  </select>
                </label>
                <label className="check">
                  <input type="checkbox" checked={gyroSourceSize} onChange={(event) => setGyroSourceSize(event.target.checked)} /> Source resolution
                </label>
                {!gyroSourceSize && (
                  <>
                    <label>
                      Width
                      <input type="number" value={gyroWidth} min={2} step={2} onChange={(event) => setGyroWidth(Number(event.target.value))} />
                    </label>
                    <label>
                      Height
                      <input type="number" value={gyroHeight} min={2} step={2} onChange={(event) => setGyroHeight(Number(event.target.value))} />
                    </label>
                  </>
                )}
                <label className="check">
                  <input type="checkbox" checked={gyroAudio} onChange={(event) => setGyroAudio(event.target.checked)} /> Gyro audio
                </label>
                <label>
                  Shared Gyroflow preset
                  <input aria-label="Shared Gyroflow preset" value={batchPreset} placeholder="Optional .gyroflow preset" onChange={(event) => setBatchPreset(event.target.value)} />
                </label>
              </>
            )}
            <button onClick={onBatch} disabled={busy || checkedClips.length === 0 || !destination}>
              Queue {checkedClips.length} {batchOp === "gyro" ? "gyro" : "export"} job{checkedClips.length === 1 ? "" : "s"}
            </button>
          </div>
          {batchOp === "export" ? (
            <p>
              Ordinary export uses each checked clip's saved trim and the preset, audio, LUT, geometry, and visual stabilization above. It does not read gyro.
            </p>
          ) : (
            <p>
              Gyro stabilization processes each whole clip with the installed Gyroflow CLI. It honors the format, resolution, audio, and optional preset here. It does not apply export trim, exposure, LUT, or visual stabilization, and it does not reuse another clip's project.
            </p>
          )}
          {batchProblems.length > 0 && (
            <ul className="problems">
              {batchProblems.map((problem) => (
                <li key={`${problem.clip_id}-${problem.code}-${problem.message}`}>
                  {clips.find((clip) => clip.id === problem.clip_id)?.name ?? problem.clip_id ?? "Batch"}: {problem.message}
                </li>
              ))}
            </ul>
          )}
          {[...new Set(jobs.map((job) => job.batch_id).filter((id): id is string => Boolean(id)))].map((batchId) => {
            const members = jobs.filter((job) => job.batch_id === batchId);
            const count = (status: string) => members.filter((job) => job.status === status).length;
            return (
              <div key={batchId}>
                <div className="counts" aria-label={`Batch ${batchId}`}>
                  <span>total {members.length}</span>
                  <span>queued {count("queued")}</span>
                  <span>running {count("running")}</span>
                  <span>succeeded {count("succeeded")}</span>
                  <span>failed {count("failed")}</span>
                  <span>cancelled {count("cancelled")}</span>
                </div>
                <div className="job-actions">
                  <button className="ghost" onClick={() => api.cancelBatch(batchId).then(refresh).catch((error: Error) => setNotice(error.message))}>
                    Cancel batch
                  </button>
                  <button className="ghost" onClick={() => api.retryBatch(batchId).then(refresh).catch((error: Error) => setNotice(error.message))}>
                    Retry failed
                  </button>
                </div>
              </div>
            );
          })}
        </section>
        <ul className="jobs">
          {jobs.length === 0 && <li className="empty">Queue is empty.</li>}
          {jobs.map((job) => {
            const request = asRecord(job.request);
            const result = asRecord(job.result);
            const filename = asText(request?.filename);
            const kind = asText(job.kind) || "job";
            const status = asText(job.status) || "unknown";
            const conversion = asText(asRecord(result?.conversion)?.message);
            const stabLabel = asText(asRecord(result?.stabilization)?.label);
            const outputPath = asText(result?.output_path);
            const errorText = asText(job.error);
            const attempt = typeof job.attempt === "number" ? job.attempt : 0;
            return (
            <li key={job.id} className={`job job-${status}`}>
              <div>
                <strong>{filename || kind}</strong>
                <span>
                  {status} · attempt {attempt} · {Math.round((Number(job.progress) || 0) * 100)}%
                  {job.batch_id ? ` · batch ${job.batch_id.slice(0, 8)}` : ""}
                </span>
                {conversion && <p>{conversion}</p>}
                {stabLabel && <p>{stabLabel}</p>}
                {errorText && <p className="error">{errorText}</p>}
                {outputPath && <p className="mono">{outputPath}</p>}
              </div>
              <div className="job-actions">
                {(status === "queued" || status === "running") && (
                  <button className="ghost" onClick={() => api.cancel(job.id).then(refresh)}>
                    Cancel
                  </button>
                )}
                {["failed", "cancelled", "interrupted", "paused"].includes(status) && (
                  <button className="ghost" onClick={() => api.retry(job.id).then(refresh)}>
                    Retry
                  </button>
                )}
              </div>
            </li>
            );
          })}
        </ul>
      </section>

      <section className="queue gyro-panel">
        <header>
          <h2>Gyro</h2>
          <p>
            This is a separate Gyroflow job. It processes the whole source or the .gyroflow project. Trim, exposure,
            saturation, LUT, resize, and vidstab/deshake from the export form are not applied.
          </p>
        </header>
        <div className="export-row">
          <label>
            Gyro log
            <input aria-label="Gyro log path" value={gyroLog} placeholder=".gcsv or other gyro log" onChange={(event) => setGyroLog(event.target.value)} />
          </label>
          <label>
            Gyroflow project
            <input aria-label="Gyroflow project path" value={gyroProject} placeholder=".gyroflow project" onChange={(event) => setGyroProject(event.target.value)} />
          </label>
          <label>
            Preset
            <input aria-label="Gyroflow preset path" value={gyroPreset} placeholder="Optional preset file" onChange={(event) => setGyroPreset(event.target.value)} />
          </label>
          <label>
            Output file
            <input aria-label="Gyro output path" value={gyroOutput} placeholder="Output .mp4 or .mov path" onChange={(event) => setGyroOutput(event.target.value)} />
          </label>
          <button
            onClick={() => {
              const body: Record<string, string> = { output_path: gyroOutput };
              if (selected) body.clip_id = selected.id;
              if (gyroLog.trim()) body.gyro_path = gyroLog.trim();
              if (gyroProject.trim()) body.project_path = gyroProject.trim();
              if (gyroPreset.trim()) body.preset_path = gyroPreset.trim();
              api
                .gyro(body)
                .then(() => {
                  setNotice("Gyroflow job queued. Missing Gyroflow or motion data fails the job. It does not use export trim or grade.");
                  return refresh();
                })
                .catch((error: Error) => setNotice(error.message));
            }}
            disabled={!gyroOutput.trim()}
          >
            Queue gyro
          </button>
          <button
            className="ghost"
            onClick={() =>
              api
                .parseGcsv(gyroLog.trim())
                .then((summary) => setGcsvText(JSON.stringify(summary, null, 2)))
                .catch((error: Error) => setGcsvText(error.message))
            }
            disabled={!gyroLog.trim()}
          >
            Read GCSV
          </button>
          <button
            className="ghost"
            onClick={() => {
              if (!selected) return;
              api
                .telemetry(selected.id)
                .then((report) => setTelemetryText(JSON.stringify(report, null, 2)))
                .catch((error: Error) => setTelemetryText(error.message));
            }}
            disabled={!selected}
          >
            Inspect telemetry
          </button>
        </div>
        {gcsvText && <pre className="mono report">{gcsvText}</pre>}
        {telemetryText && <pre className="mono report">{telemetryText}</pre>}
      </section>

      {showSettings && caps && (
        <aside className="settings">
          <h2>Capabilities</h2>
          <p>Missing tools are installation gaps, not a paid gate. Nothing here is bundled.</p>
          <ToolLine name="FFmpeg" tool={caps.ffmpeg} />
          <ToolLine name="ffprobe" tool={caps.ffprobe} />
          <ToolLine name="Gyroflow" tool={caps.gyroflow} />
          <p>{caps.telemetry_parser.detail}</p>
          <p>{caps.vidstab_auto.meaning}</p>
          <p>HDR display matching and Sony color transforms are not implemented.</p>
          <label>
            ffmpeg path
            <input value={ffmpegPath} onChange={(event) => setFfmpegPath(event.target.value)} />
          </label>
          <label>
            ffprobe path
            <input value={ffprobePath} onChange={(event) => setFfprobePath(event.target.value)} />
          </label>
          <label>
            Gyroflow path
            <input value={gyroPath} onChange={(event) => setGyroPath(event.target.value)} />
          </label>
          <label>
            crafts-telemetry path
            <input value={telemetryPath} onChange={(event) => setTelemetryPath(event.target.value)} />
          </label>
          <button
            onClick={() =>
              api
                .settings({
                  ffmpeg: ffmpegPath,
                  ffprobe: ffprobePath,
                  gyroflow: gyroPath,
                  telemetry_parser: telemetryPath,
                })
                .then(refresh)
                .then(() => setNotice("Tool paths saved."))
                .catch((error: Error) => setNotice(error.message))
            }
          >
            Save tool paths
          </button>
          <h3>Project file</h3>
          <input aria-label="Project file path" value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder="bench.crafts.json" />
          <div className="job-actions">
            <button className="ghost" onClick={() => api.saveProject(projectPath).then(refresh).catch((error: Error) => setNotice(error.message))}>
              Save
            </button>
            <button className="ghost" onClick={() => api.openProject(projectPath).then(refresh).catch((error: Error) => setNotice(error.message))}>
              Open
            </button>
          </div>
          {selected && !selected.browser_playable && (
            <button
              className="ghost"
              onClick={() =>
                api.proxy(selected.id).then((proxy) => {
                  if (proxy.kind === "proxy") {
                    setPreviewKind("proxy");
                    setPreviewUrl(proxy.url);
                    const limit = proxy.duration_limit_seconds ?? 60;
                    setPreviewNote(`${proxy.note} Playback here is capped at ${limit} seconds.`);
                  } else {
                    setPreviewKind("source");
                    setPreviewUrl(proxy.url);
                    setPreviewNote(proxy.note);
                  }
                }).catch((error: Error) => setNotice(error.message))
              }
            >
              Create viewing proxy
            </button>
          )}
        </aside>
      )}
    </div>
  );
}

function Chip({ ok, label }: { ok: boolean; label: string }) {
  return <span className={ok ? "chip ok" : "chip miss"}>{label}</span>;
}

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      {label}
      <input type="number" value={value} min={min} max={max} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}

function ToolLine({ name, tool }: { name: string; tool: { available: boolean; version?: string | null; detail: string } }) {
  return (
    <p>
      <strong>{name}:</strong> {tool.available ? tool.version || "available" : "unavailable"}
      <span className="tool-detail">{tool.detail}</span>
    </p>
  );
}
