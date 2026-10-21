import { useEffect, useState } from "react";

/** State remembered in this browser (the step and chapter last open in a project),
 * so a reload comes back to the same place. Storage can be unavailable (a private
 * window, blocked site data): then it's plain state. */
export function useStored<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // not remembered; nothing else depends on it
    }
  }, [key, value]);
  return [value, setValue];
}
