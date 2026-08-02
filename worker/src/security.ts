const encoder = new TextEncoder();

async function digest(value: string): Promise<Uint8Array> {
  const hash = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return new Uint8Array(hash);
}

export async function secureEquals(candidate: string | null, expected: string): Promise<boolean> {
  if (candidate === null) {
    return false;
  }
  const [left, right] = await Promise.all([digest(candidate), digest(expected)]);
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left[index]! ^ right[index]!;
  }
  return difference === 0;
}
