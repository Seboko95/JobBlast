import type { ApiSettings, DocumentItem, HomePayload, Job, Source } from "./types";

export class JobBlastApi {
  constructor(private readonly settings: ApiSettings) {}

  private url(path: string) {
    return `${this.settings.baseUrl.replace(/\/$/, "")}${path}`;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${this.settings.token}`);
    if (init.body && !(init.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    const response = await fetch(this.url(path), { ...init, headers });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Request failed: ${response.status}`);
    }
    return response.json() as Promise<T>;
  }

  home() {
    return this.request<HomePayload>("/v1/home");
  }

  profile() {
    return this.request<{ profile: Record<string, unknown> }>("/v1/profile");
  }

  updateProfile(profile: Record<string, unknown>) {
    return this.request<{ profile: Record<string, unknown> }>("/v1/profile", {
      method: "PUT",
      body: JSON.stringify({ profile }),
    });
  }

  jobs(listName: "top" | "new" | "pipeline" | "public" | "letters") {
    return this.request<{ jobs: Job[] }>(`/v1/jobs?list_name=${listName}`);
  }

  job(jobId: string) {
    return this.request<{ job: Job }>(`/v1/jobs/${encodeURIComponent(jobId)}`);
  }

  action(jobId: string, action: "save" | "skip" | "mark-applied") {
    return this.request<{ job: Job }>(`/v1/jobs/${encodeURIComponent(jobId)}/${action}`, { method: "POST" });
  }

  removeJob(jobId: string) {
    return this.request<{ removed: boolean }>(`/v1/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
  }

  scan() {
    return this.request<{ new_jobs: number; public_matches: number; errors: unknown[]; warnings: unknown[] }>("/v1/scan", {
      method: "POST",
    });
  }

  generated(jobId: string, kind: "cover-letter" | "answers" | "gap-analysis") {
    return this.request<{ text: string; package?: Record<string, unknown> }>(`/v1/jobs/${encodeURIComponent(jobId)}/${kind}`, {
      method: "POST",
    });
  }

  documents() {
    return this.request<{ resume_status: string; document_summary: string; documents: DocumentItem[] }>("/v1/documents");
  }

  upload(path: "/v1/resume" | "/v1/documents", file: { uri: string; name: string; mimeType?: string }) {
    const body = new FormData();
    body.append("file", {
      uri: file.uri,
      name: file.name,
      type: file.mimeType || "application/octet-stream",
    } as unknown as Blob);
    return this.request<unknown>(path, { method: "POST", body });
  }

  sources() {
    return this.request<{ sources: Source[]; stats: Record<string, number> }>("/v1/sources");
  }

  updateSource(sourceId: string, enabled: boolean) {
    return this.request<{ source: Source }>(`/v1/sources/${encodeURIComponent(sourceId)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    });
  }
}

