import assert from "node:assert/strict";
import test from "node:test";
import { nextChecked } from "./selection.ts";

test("opening another project clears checks even when clip ids match", () => {
  const ids = ["c0216", "c0218"];
  assert.deepEqual(nextChecked(ids, "/bench/a.crafts.json", "/bench/b.crafts.json", ids), []);
});

test("an ordinary refresh keeps checks and drops stale ids", () => {
  assert.deepEqual(nextChecked(["keep", "gone"], "/bench/a.crafts.json", "/bench/a.crafts.json", ["keep"]), ["keep"]);
});

test("the first load and the first save do not clear a selection", () => {
  assert.deepEqual(nextChecked(["keep"], undefined, "", ["keep"]), ["keep"]);
  assert.deepEqual(nextChecked(["keep"], "", "/bench/a.crafts.json", ["keep"]), ["keep"]);
});
