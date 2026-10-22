// Loads pai/static/md.js and then every pai/static/app/*.js part, in the order
// index.html includes them, into one shared context (as a browser does). A
// permissive DOM stub lets top-level statements run; a ReferenceError or
// TypeError at load means a part depends on code from a later part.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.join(here, "../../pai/static");

function stub() {
  const target = function () {};
  const handler = {
    get(_, key) {
      if (key === Symbol.toPrimitive) return () => "";
      if (key === "then") return undefined;
      if (key === Symbol.iterator) return function* () {};
      return proxy;
    },
    set: () => true,
    apply: () => proxy,
    construct: () => proxy,
    has: () => true,
  };
  const proxy = new Proxy(target, handler);
  return proxy;
}

function freshContext() {
  const ctx = {};
  for (const name of ["document", "window", "localStorage", "sessionStorage", "navigator",
                      "location", "history", "matchMedia", "visualViewport", "Image"]) {
    ctx[name] = stub();
  }
  ctx.window = ctx;
  ctx.setTimeout = () => 0;
  ctx.setInterval = () => 0;
  ctx.clearTimeout = () => {};
  ctx.clearInterval = () => {};
  ctx.requestAnimationFrame = () => 0;
  ctx.fetch = () => new Promise(() => {});
  return vm.createContext(ctx);
}

const partsInOrder = readdirSync(path.join(staticDir, "app"))
  .filter((f) => f.endsWith(".js"))
  .sort();

test("app parts are named so that lexical order equals include order", () => {
  assert.deepEqual(partsInOrder, [...partsInOrder].sort());
  assert.ok(partsInOrder.length >= 2);
});

test("md.js and every app part load in order without top-level errors", () => {
  const ctx = freshContext();
  vm.runInContext(readFileSync(path.join(staticDir, "md.js"), "utf8"), ctx, { filename: "md.js" });
  for (const part of partsInOrder) {
    const src = readFileSync(path.join(staticDir, "app", part), "utf8");
    assert.doesNotThrow(() => vm.runInContext(src, ctx, { filename: part }), part);
  }
});

test("theme-init and viewport-fix load without top-level errors", () => {
  const ctx = freshContext();
  for (const f of ["theme-init.js", "viewport-fix.js"]) {
    const src = readFileSync(path.join(staticDir, f), "utf8");
    assert.doesNotThrow(() => vm.runInContext(src, ctx, { filename: f }), f);
  }
});
