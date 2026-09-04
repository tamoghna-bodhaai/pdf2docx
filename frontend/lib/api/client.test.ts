import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("typed API adapter", () => {
  it("normalizes FastAPI validation details", async () => {
    const response = new Response(JSON.stringify({ detail: [{ msg: "Start page is required" }, { msg: "End page is invalid" }] }), {
      status: 422, headers: { "content-type": "application/json" },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(api.config()).rejects.toEqual(expect.objectContaining({
      status: 422, message: "Start page is required · End page is invalid",
    }));
  });

  it("uses same-origin credentials and encodes identifiers", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "ok" }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await api.job("job with/slash");

    expect(fetch).toHaveBeenCalledWith("/api/jobs/job%20with%2Fslash", expect.objectContaining({ credentials: "same-origin" }));
    expect(api.downloadUrl("a/b", "pdf")).toBe("/api/jobs/a%2Fb/download?format=pdf");
  });

  it("turns network failures into a status-zero error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    await expect(api.history()).rejects.toEqual(expect.objectContaining({ status: 0, message: "offline" }));
  });
});
