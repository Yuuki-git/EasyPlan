export function createLatestRequestGate() {
  let latest = 0;
  return {
    begin() {
      const sequence = ++latest;
      return () => sequence === latest;
    },
    invalidate() {
      latest += 1;
    },
  };
}
