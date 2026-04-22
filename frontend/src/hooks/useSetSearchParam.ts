import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

/** Returns a setter that writes `value` into `?key=value`, deleting the key
 *  when `value === defaultValue` so default states produce clean URLs. Always
 *  uses history.replace so filter toggles don't clutter the back stack. */
export function useSetSearchParam() {
  const [, setSearchParams] = useSearchParams();
  return useCallback(
    (key: string, value: string, defaultValue: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === defaultValue) next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );
}
