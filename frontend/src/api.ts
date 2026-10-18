async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail = payload.detail || response.statusText;
    const error = new Error(detail) as Error & { code?: string; status?: number; problems?: { clip_id: string | null; code: string; message: string }[] };
    error.code = payload.code;
    error.status = response.status;
    error.problems = payload.problems;
    throw error;
  }
  return payload as T;
}

export const api = {
  health: () => request<{ ok: boolean }>("/api/health"),
  capabilities: () => request<import("./types").Capabilities>("/api/capabilities"),
  project: () => request<{ name: string; path: string | null; clips: import("./types").Clip[]; jobs: import("./types").Job[] }>("/api/project"),
  saveProject: (path: string) => request("/api/project/save", { method: "POST", body: JSON.stringify({ path }) }),
  openProject: (path: string) => request("/api/project/open", { method: "POST", body: JSON.stringify({ path }) }),
  rename: (name: string) => request("/api/project/name", { method: "POST", body: JSON.stringify({ name }) }),
  importMedia: (path: string) =>
    request<{ added: import("./types").Clip[]; duplicates: { id: string }[]; skipped: { path: string; reason: string }[] }>(
      "/api/media/import",
      { method: "POST", body: JSON.stringify({ path }) },
    ),
  patchClip: (id: string, body: unknown) => request<import("./types").Clip>(`/api/media/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  jobs: () => request<{ jobs: import("./types").Job[] }>("/api/jobs"),
  exportJob: (body: unknown) => request<import("./types").Job>("/api/jobs/export", { method: "POST", body: JSON.stringify(body) }),
  cancel: (id: string) => request(`/api/jobs/${id}/cancel`, { method: "POST" }),
  retry: (id: string) => request(`/api/jobs/${id}/retry`, { method: "POST" }),
  proxy: (id: string) =>
    request<{ kind: string; url: string; note: string; duration_limit_seconds: number | null; limited: boolean }>(
      `/api/media/${id}/proxy`,
      { method: "POST" },
    ),
  gyro: (body: unknown) => request<import("./types").Job>("/api/jobs/gyroflow", { method: "POST", body: JSON.stringify(body) }),
  batch: (body: unknown) =>
    request<{ batch_id: string; count: number; queued: import("./types").Job[] }>("/api/jobs/batch", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  batchStatus: (id: string) => request<import("./types").BatchStatus>(`/api/jobs/batch/${id}`),
  cancelBatch: (id: string) => request(`/api/jobs/batch/${id}/cancel`, { method: "POST" }),
  retryBatch: (id: string) => request(`/api/jobs/batch/${id}/retry`, { method: "POST" }),
  parseGcsv: (path: string) => request<Record<string, unknown>>("/api/gcsv/parse", { method: "POST", body: JSON.stringify({ path }) }),
  telemetry: (id: string) => request<Record<string, unknown>>(`/api/media/${id}/telemetry`, { method: "POST" }),
  settings: (body: unknown) => request("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
};
