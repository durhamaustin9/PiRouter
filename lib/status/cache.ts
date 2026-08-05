type CacheEntry<T> = {
  expiresAt: number;
  value: T;
};

/** Coalesces concurrent probes and reuses their result for a short TTL. */
export function createCachedProbe<T>(ttlMs: number) {
  let cached: CacheEntry<T> | null = null;
  let inFlight: Promise<T> | null = null;

  return async (load: () => Promise<T>): Promise<T> => {
    const now = Date.now();
    if (cached && cached.expiresAt > now) return cached.value;
    if (inFlight) return inFlight;

    inFlight = load()
      .then((value) => {
        cached = { value, expiresAt: Date.now() + ttlMs };
        return value;
      })
      .catch((error) => {
        if (cached) return cached.value;
        throw error;
      })
      .finally(() => {
        inFlight = null;
      });

    return inFlight;
  };
}
