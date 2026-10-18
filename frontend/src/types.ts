export type Clip = {
  id: string;
  path: string;
  name: string;
  sha256: string;
  size: number;
  flag: "" | "OK" | "KEEP" | "NG";
  notes: string;
  markers: { id: string; seconds: number; label: string }[];
  trim_in: number;
  trim_out: number;
  browser_playable: boolean;
  probe: {
    duration: number | null;
    timecode: string | null;
    tags: Record<string, string>;
    video: VideoStream[];
    audio: { codec: string | null; sample_rate: number | null; channels: number | null }[];
    data: { codec: string | null; codec_tag: string | null }[];
    motion_metadata: { status: string; note: string };
  };
  summary: {
    codec: string | null;
    width: number | null;
    height: number | null;
    pix_fmt: string | null;
    bit_depth: number | null;
    frame_rate: string | null;
    duration: number | null;
    timecode: string | null;
    audio_codecs: (string | null)[];
    data_streams: number;
  };
};

export type VideoStream = {
  codec: string | null;
  profile: string | null;
  width: number | null;
  height: number | null;
  pix_fmt: string | null;
  bit_depth: number | null;
  frame_rate: string | null;
  color_space: string | null;
  color_transfer: string | null;
  color_primaries: string | null;
  color_range: string | null;
  tags: Record<string, string>;
};

export type Job = {
  id: string;
  kind: string;
  status: string;
  progress: number;
  error: string | null;
  code: string | null;
  attempt: number;
  result: {
    output_path?: string;
    filter_chain?: string;
    conversion?: { message: string; bit_depth_conversion: boolean };
    stabilization?: { label?: string; mode: string; kind: string };
    validation?: Record<string, unknown>;
  } | null;
  request: { filename?: string; preset_id?: string; conversion?: { message: string } };
};

export type Capabilities = {
  ffmpeg: { available: boolean; version: string | null; detail: string; encoders?: Record<string, boolean>; filters?: Record<string, boolean> };
  ffprobe: { available: boolean; version: string | null; detail: string };
  gyroflow: { available: boolean; version: string | null; detail: string };
  telemetry_parser: { available: boolean; detail: string };
  presets: { id: string; label: string; available: boolean; bit_depth: number; pix_fmt: string; reason: string | null }[];
  vidstab_auto: { shakiness: number; accuracy: number; smoothing: number; crop: string; zoom: number; optzoom: number; meaning: string };
  limits: Record<string, boolean>;
};
