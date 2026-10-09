import { beforeEach } from "vitest";
import { clearRecent, loadRecent, remember } from "./history";

beforeEach(() => localStorage.clear());

test("keeps one entry per reel URL, newest first", () => {
  remember({ id: "job1", url: "https://ex.com/reel", at: 1 });
  remember({ id: "job2", url: "https://ex.com/other", at: 2 });
  const list = remember({ id: "job3", url: "https://ex.com/reel", at: 3 });

  expect(list.map((r) => r.id)).toEqual(["job3", "job2"]);
  expect(loadRecent().map((r) => r.id)).toEqual(["job3", "job2"]);
});

test("drops duplicates already in storage", () => {
  localStorage.setItem(
    "claimlens.recent",
    JSON.stringify([
      { id: "new", url: "https://ex.com/reel", at: 2 },
      { id: "old", url: "https://ex.com/reel", at: 1 },
    ]),
  );

  expect(loadRecent().map((r) => r.id)).toEqual(["new"]);
});

test("clearRecent empties the list", () => {
  remember({ id: "job1", url: "https://ex.com/reel", at: 1 });
  clearRecent();

  expect(loadRecent()).toEqual([]);
});
