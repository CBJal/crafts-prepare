import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { Capabilities, Clip, Job } from "./types";

const FLAGS = ["", "OK", "KEEP", "NG"] as const;

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
  const [previewNote, setPreviewNote] = useState("");
  const [ffmpegPath, setFfmpegPath] = useState("ffmpeg");
  const [ffprobePath, setFfprobePath] = useState("ffprobe");
  const [gyroPath, setGyroPath] = useState("gyroflow");
  const [telemetryPath, setTelemetryPath] = useState("");

  const selected = clips.find((clip) => clip.id === selectedId) ?? null;

  async function refresh() {
    const [nextCaps, project, queue] = await Promise.all([api.capabilities(), api.project(), api.jobs()]);
    setCaps(nextCaps);
    setClips(project.clips);
    setJobs(queue.jobs);
    setProjectName(project.name);
    if (project.path) setProjectPath(project.path);
    setSelectedId((current) => current ?? project.clips[0]?.id ?? null);
  }

  useEffect(() => {
    refresh().catch((error: Error) => setNotice(error.message));
  }, []);

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
      return;
    }
    if (selected.browser_playable) {
      setPreviewUrl(`/api/media/${selected.id}/file`);
      setPreviewNote("This player shows the source file. Exposure, saturation, and LUT stills use the same FFmpeg filters as export. Vidstab and deshake run only in the export.");
      return;
    }
    setPreviewUrl(`/api/media/${selected.id}/thumbnail?t=0`);
    setPreviewNote("This container is not played in the browser. The still is an FFmpeg frame. Create a viewing proxy if you want motion playback.");
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
    return `/api/media/${selected.id}/thumbnail?${params.toString()}`;
  }, [selected, exposure, saturation, rotate, fit, width, height]);

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

  const video = selected?.probe.video[0];
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
          <ul>
            {clips.length === 0 && <li className="empty">No clips yet.</li>}
            {clips.map((clip) => (
              <li key={clip.id}>
                <button className={clip.id === selectedId ? "clip active" : "clip"} onClick={() => setSelectedId(clip.id)}>
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
          </header>
          {selected && previewUrl && selected.browser_playable && (
            <video key={previewUrl} src={previewUrl} controls preload="metadata" />
          )}
          {selected && !selected.browser_playable && <img src={previewUrl ?? ""} alt="Frame from the selected clip" />}
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
                {video.codec} {video.profile ? `· ${video.profile}` : ""}
              </dd>
              <dt>Frame</dt>
              <dd>
                {video.width}×{video.height} · {video.pix_fmt} · {video.bit_depth ?? "unknown"}-bit · {video.frame_rate}
              </dd>
              <dt>Duration</dt>
              <dd>{selected.summary.duration ?? "unknown"} s</dd>
              <dt>Timecode tag</dt>
              <dd>{selected.probe.timecode ?? "none in the probed tags"}</dd>
              <dt>Color</dt>
              <dd>
                {[video.color_primaries, video.color_transfer, video.color_space, video.color_range].filter(Boolean).join(" · ") || "no color tags"}
              </dd>
              <dt>Audio</dt>
              <dd>{selected.summary.audio_codecs.filter(Boolean).join(", ") || "none"}</dd>
              <dt>Data streams</dt>
              <dd>
                {selected.probe.data.length === 0
                  ? "ffprobe listed none. That is not a claim that gyro metadata is absent."
                  : selected.probe.data.map((stream) => stream.codec || stream.codec_tag || "data").join(", ")}
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
        <ul className="jobs">
          {jobs.length === 0 && <li className="empty">Queue is empty.</li>}
          {jobs.map((job) => (
            <li key={job.id} className={`job job-${job.status}`}>
              <div>
                <strong>{job.request.filename || job.kind}</strong>
                <span>
                  {job.status} · attempt {job.attempt} · {Math.round((job.progress || 0) * 100)}%
                </span>
                {job.result?.conversion?.message && <p>{job.result.conversion.message}</p>}
                {job.result?.stabilization?.label && <p>{job.result.stabilization.label}</p>}
                {job.error && <p className="error">{job.error}</p>}
                {job.result?.output_path && <p className="mono">{job.result.output_path}</p>}
              </div>
              <div className="job-actions">
                {(job.status === "queued" || job.status === "running") && (
                  <button className="ghost" onClick={() => api.cancel(job.id).then(refresh)}>
                    Cancel
                  </button>
                )}
                {["failed", "cancelled", "interrupted"].includes(job.status) && (
                  <button className="ghost" onClick={() => api.retry(job.id).then(refresh)}>
                    Retry
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
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
                  setPreviewUrl(proxy.url);
                  setPreviewNote(proxy.note);
                })
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
