// Server-side credential broker. This function never ships parent R2 secrets
// or the encryption master to the desktop app.
import { createClient } from "npm:@supabase/supabase-js@2.102.0";

type Device = {
  id: string;
  user_id: string;
  device_name: string;
  active: boolean;
  created_at: string;
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function required(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error("Missing server configuration: " + name);
  return value;
}
function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}
async function passwordFor(master: string, userId: string, deviceId: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(master),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key,
    new TextEncoder().encode("autobackup-restic-v1:" + userId + ":" + deviceId)));
  return btoa(String.fromCharCode(...signature)).replaceAll("+", "-")
    .replaceAll("/", "_").replaceAll("=", "");
}
Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST") return response({ error: "Method not allowed" }, 405);
  const authorization = request.headers.get("authorization") ?? "";
  if (!authorization.startsWith("Bearer ") || authorization.length > 5000)
    return response({ error: "Authentication required" }, 401);

  try {
    // Key is confidential: use the server-only service role, never the
    // publishable/anon key. getUser verifies the user's bearer JWT.
    const base = required("SUPABASE_URL");
    const service = required("SUPABASE_SERVICE_ROLE_KEY");
    const supabase = createClient(base, service,
      { auth: { persistSession: false, autoRefreshToken: false } });
    const { data: authData, error: authError } =
      await supabase.auth.getUser(authorization.slice(7));
    const user = authData.user;
    if (authError || !user) return response({ error: "Invalid session" }, 401);
    // app_metadata is admin-controlled; user_metadata is NOT trusted.
    if (user.app_metadata?.autobackup_enabled !== true)
      return response({ error: "Account disabled or unprovisioned" }, 403);

    const body = await request.json().catch(() => null);
    if (!body || typeof body !== "object" || Array.isArray(body))
      return response({ error: "Invalid request" }, 400);
    const action = String(body.action ?? "");
    if (!["register_device", "list_devices", "grant"].includes(action))
      return response({ error: "Unknown action" }, 400);

    if (action === "list_devices") {
      const { data, error } = await supabase.from("autobackup_devices")
        .select("id,device_name,created_at")
        .eq("user_id", user.id).eq("active", true).order("created_at", { ascending: false });
      if (error) throw error;
      return response({ devices: data });
    }

    const deviceId = String(body.device_id ?? "");
    if (!UUID.test(deviceId)) return response({ error: "Invalid device" }, 400);
    const { data: existing, error: selectError } =
      await supabase.from("autobackup_devices").select("*")
      .eq("id", deviceId).maybeSingle();
    if (selectError) throw selectError;
    if (existing && existing.user_id !== user.id)
      return response({ error: "Device not owned by user" }, 403);

    if (action === "register_device") {
      if (existing) {
        if (!existing.active) return response({ error: "Device disabled" }, 403);
        return response({ ok: true });
      }
      const name = String(body.device_name ?? "").trim();
      if (!name || name.length > 80) return response({ error: "Invalid device name" }, 400);
      // Keep accidental unlimited-device registration bounded. This is NOT
      // a storage quota; enforce quotas separately for commercial use.
      const { count, error: countError } = await supabase.from("autobackup_devices")
        .select("id", { count: "exact", head: true }).eq("user_id", user.id);
      if (countError) throw countError;
      if ((count ?? 0) >= 20) return response({ error: "Device limit reached" }, 403);
      const { error } = await supabase.from("autobackup_devices")
        .insert({ id: deviceId, user_id: user.id, device_name: name });
      if (error) return response({ error: "Unable to register device" }, 409);
      return response({ ok: true });
    }

    if (!existing || !existing.active)
      return response({ error: "Device not found or disabled" }, 403);

    const account = required("CF_ACCOUNT_ID");
    const bucket = required("CF_R2_BUCKET");
    const parentToken = required("CF_R2_API_TOKEN");
    const parentAccessId = required("CF_R2_PARENT_ACCESS_KEY_ID");
    const master = required("RESTIC_MASTER_KEY");
    if (!/^[0-9a-f]{32}$/i.test(account) || !/^[a-z0-9.-]{3,63}$/.test(bucket) || master.length < 40)
      throw new Error("Invalid private server configuration");
    const region = Deno.env.get("CF_R2_REGION") ?? "global";
    if (!["global", "eu"].includes(region)) throw new Error("Invalid R2 region");
    const prefix = "users/" + user.id + "/devices/" + deviceId + "/";
    const ttl = Number(Deno.env.get("R2_TTL_SECONDS") ?? "172800");
    if (!Number.isInteger(ttl) || ttl < 3600 || ttl > 604800)
      throw new Error("Invalid R2 temporary credential lifetime");
    const cloudflare = await fetch(
      "https://api.cloudflare.com/client/v4/accounts/" + account + "/r2/temp-access-credentials",
      {
        method: "POST",
        headers: { authorization: "Bearer " + parentToken, "content-type": "application/json" },
        body: JSON.stringify({
          bucket, parentAccessKeyId: parentAccessId,
          permission: "object-read-write", ttlSeconds: ttl, prefixes: [prefix],
        }),
      });
    if (!cloudflare.ok) throw new Error("Cloudflare credential request failed");
    const cf = await cloudflare.json();
    if (!cf.success || !cf.result?.accessKeyId || !cf.result?.secretAccessKey ||
        !cf.result?.sessionToken) throw new Error("Cloudflare temporary credentials unavailable");
    const endpoint = "https://" + account +
      (region === "eu" ? ".eu" : "") + ".r2.cloudflarestorage.com";
    return response({
      repository: "s3:" + endpoint + "/" + bucket + "/" + prefix.slice(0, -1),
      access_key_id: cf.result.accessKeyId,
      secret_access_key: cf.result.secretAccessKey,
      session_token: cf.result.sessionToken,
      password: await passwordFor(master, user.id, deviceId),
      expires_in_seconds: ttl,
    });
  } catch (_error) {
    // Never return R2, Auth, or crypto secret values or Cloudflare responses.
    return response({ error: "Credential service unavailable" }, 503);
  }
});
