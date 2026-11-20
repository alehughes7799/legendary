import { describe, expect, it } from "vitest";
import { mergeLibrary } from "./library";
import { errorMessage, type InstalledGame } from "./api";

const local: InstalledGame = {
  appName: "installed",
  title: "Installed game",
  version: "1",
  platform: "Windows",
  installPath: "/games/example",
  installSize: 1024,
  canRunOffline: true,
  needsVerification: false,
  executable: true,
};

describe("library reconciliation", () => {
  it("keeps installed games when remote library is unavailable", () => {
    expect(mergeLibrary([], [local])[0].installation).toEqual(local);
  });

  it("merges by app name and retains remote covers and local versions", () => {
    const result = mergeLibrary(
      [
        {
          ...local,
          version: "2",
          coverUrl: "https://cdn.epicgames.com/cover.jpg",
        },
      ],
      [local],
    );
    expect(result).toHaveLength(1);
    expect(result[0].coverUrl).toContain("cover.jpg");
    expect(result[0].installation?.version).toBe("1");
  });
});

describe("user-facing errors", () => {
  it("translates stable backend codes and handles unstructured failures", () => {
    expect(errorMessage({ code: "AUTH_REQUIRED" })).toContain("登录");
    expect(errorMessage(new Error("internal details"))).not.toContain(
      "internal details",
    );
  });
});
