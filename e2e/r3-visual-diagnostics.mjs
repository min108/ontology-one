import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

export function describePng(bytes) {
  if (
    !Buffer.isBuffer(bytes) ||
    bytes.length < 33 ||
    !bytes
      .subarray(0, 8)
      .equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) ||
    bytes.toString("ascii", 12, 16) !== "IHDR"
  )
    throw new Error("Invalid PNG visual artifact");
  const width = bytes.readUInt32BE(16),
    height = bytes.readUInt32BE(20);
  if (!width || !height) throw new Error("Invalid PNG visual dimensions");
  return {
    width,
    height,
    bytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
}
// D8/D9: a baseline remains mandatory and immutable in compare mode. Pixel
// differences are review information; native geometry and axe remain hard gates.
export function compareVisualFiles(actual, baselinePath) {
  const current = describePng(actual),
    baseline = describePng(readFileSync(baselinePath));
  return {
    mode: "review-only",
    actual: current,
    baseline,
    identical: current.sha256 === baseline.sha256,
    dimensions_equal:
      current.width === baseline.width && current.height === baseline.height,
  };
}
