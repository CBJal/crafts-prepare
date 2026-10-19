/** Decide which library checks survive a refresh. Same clip ids in another project are not kept. */

export function nextChecked(
  current: string[],
  previousPath: string | undefined,
  nextPath: string,
  clipIds: string[],
): string[] {
  const switched = previousPath !== undefined && previousPath !== "" && nextPath !== "" && previousPath !== nextPath;
  if (switched) return [];
  const ids = new Set(clipIds);
  return current.filter((id) => ids.has(id));
}
