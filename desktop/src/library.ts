import type { InstalledGame, LibraryGame } from "./api";

export interface Game extends LibraryGame {
  installation?: InstalledGame;
}

export function mergeLibrary(
  library: LibraryGame[],
  installed: InstalledGame[],
): Game[] {
  const games = new Map<string, Game>(
    library.map((game) => [game.appName, { ...game }]),
  );
  for (const installation of installed) {
    const game = games.get(installation.appName) ?? installation;
    games.set(installation.appName, { ...game, installation });
  }
  return [...games.values()].sort((a, b) =>
    a.title.localeCompare(b.title, "zh-CN"),
  );
}

export function formatSize(bytes: number): string {
  return bytes > 0 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : "大小未知";
}
