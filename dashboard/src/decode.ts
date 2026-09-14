// Checking an API body field by field instead of casting it.
//
// Every response arrives as `unknown`. A cast would let a renamed or missing
// field reach the page as `undefined`; these helpers turn it into an error that
// names the field, which is the difference between a blank cell and a bug report.

export class DecodeError extends Error {
  constructor(path: string, expected: string) {
    super(`${path}: expected ${expected}`);
    this.name = "DecodeError";
  }
}

export type Fields = Record<string, unknown>;

export function asObject(value: unknown, path: string): Fields {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new DecodeError(path, "an object");
  }
  return value as Fields;
}

export function asString(fields: Fields, key: string, path: string): string {
  const value = fields[key];
  if (typeof value !== "string") {
    throw new DecodeError(`${path}.${key}`, "a string");
  }
  return value;
}

export function asNullableString(fields: Fields, key: string, path: string): string | null {
  return fields[key] === null ? null : asString(fields, key, path);
}

export function asNumber(fields: Fields, key: string, path: string): number {
  const value = fields[key];
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new DecodeError(`${path}.${key}`, "a finite number");
  }
  return value;
}

export function asBoolean(fields: Fields, key: string, path: string): boolean {
  const value = fields[key];
  if (typeof value !== "boolean") {
    throw new DecodeError(`${path}.${key}`, "a boolean");
  }
  return value;
}

export function asOneOf<T extends string>(
  fields: Fields,
  key: string,
  allowed: readonly T[],
  path: string,
): T {
  const value = asString(fields, key, path);
  const match = allowed.find((candidate) => candidate === value);
  if (match === undefined) {
    throw new DecodeError(`${path}.${key}`, `one of ${allowed.join(", ")}`);
  }
  return match;
}
