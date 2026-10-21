// Every write carries X-BSync: 1. The server refuses writes without it, which is
// what stops another website from driving this API through the user’s browser
// (see packages/app/server.py).

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function handle<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let detail = `Request failed (${response.status})`;
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") detail = body.detail;
  } catch {
    /* not JSON */
  }
  throw new ApiError(detail, response.status);
}

export async function get<T>(path: string): Promise<T> {
  try {
    return await handle<T>(await fetch(path, { cache: "no-store" }));
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Can’t reach Tripwire. Is the app still running?", 0);
  }
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  try {
    return await handle<T>(
      await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-BSync": "1" },
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    );
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Can’t reach Tripwire. Is the app still running?", 0);
  }
}

export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  try {
    return await handle<T>(await fetch(path, { method: "POST", headers: { "X-BSync": "1" }, body: form }));
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Can’t reach Tripwire. Is the app still running?", 0);
  }
}
