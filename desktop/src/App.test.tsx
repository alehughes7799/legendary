// @vitest-environment jsdom
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import App from "./App";
import { api, type InstalledGame, type InstallTask } from "./api";

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  desktopAvailable: true,
  api: {
    status: vi.fn(),
    installed: vi.fn(),
    library: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
    launch: vi.fn(),
    openLogin: vi.fn(),
    installStatus: vi.fn(),
    installOptions: vi.fn(),
    prepareInstall: vi.fn(),
    prepareImport: vi.fn(),
    startInstall: vi.fn(),
    cancelInstall: vi.fn(),
    chooseInstallDirectory: vi.fn(),
  },
}));

const game: InstalledGame = {
  appName: "local-game",
  title: "Local Adventure",
  version: "1",
  platform: "Windows",
  installPath: "/games/adventure",
  installSize: 1024 ** 3,
  canRunOffline: true,
  needsVerification: false,
  executable: true,
};

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
});

beforeEach(() => {
  vi.mocked(api.installStatus).mockResolvedValue({ task: null });
  vi.mocked(api.status).mockResolvedValue({
    hasCredentials: false,
    displayName: null,
    defaultPlatform: "Windows",
  });
  vi.mocked(api.installed).mockResolvedValue({ games: [game] });
  vi.mocked(api.library).mockResolvedValue({ games: [] });
  vi.mocked(api.launch).mockResolvedValue({ appName: game.appName, pid: 123 });
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("desktop workflows", () => {
  it("shows local installations before signing in and filters search results", async () => {
    render(<App />);
    await screen.findByRole("button", { name: game.title });
    expect(api.library).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索游戏" }), {
      target: { value: "no match" },
    });
    expect(screen.queryByRole("button", { name: game.title })).toBeNull();
    expect(screen.getByText("没有找到匹配的游戏")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "清空搜索" }));
    expect(screen.getByRole("button", { name: game.title })).toBeTruthy();
  });

  it("logs in with a pasted code then loads the remote library", async () => {
    vi.mocked(api.login).mockResolvedValue({
      hasCredentials: true,
      displayName: "Player",
      defaultPlatform: "Windows",
    });
    render(<App />);
    await screen.findByRole("button", { name: game.title });
    fireEvent.click(screen.getByRole("button", { name: "连接 Epic Games" }));
    fireEvent.change(screen.getByLabelText("授权码"), {
      target: { value: "temporary-code" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登录并同步游戏库" }));
    await waitFor(() =>
      expect(api.library).toHaveBeenCalledWith("Windows", false),
    );
    expect(api.login).toHaveBeenCalledWith("temporary-code");
    expect(screen.queryByLabelText("授权码")).toBeNull();
    expect(screen.queryByText("temporary-code")).toBeNull();
  });

  it("passes explicit offline options and reports a successful launch", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: game.title }));
    fireEvent.click(screen.getByRole("checkbox", { name: "离线启动" }));
    fireEvent.click(screen.getByRole("button", { name: "启动游戏" }));
    await waitFor(() =>
      expect(api.launch).toHaveBeenCalledWith(game.appName, true, false),
    );
    await screen.findByText(`已发送 ${game.title} 的启动请求。`);
  });

  it.each([
    ["NETWORK_ERROR", "网络"],
    ["TLS_ERROR", "安全连接"],
    ["AUTH_CODE_INVALID", "授权码"],
  ])("reports the cause of a failed login: %s", async (code, message) => {
    vi.mocked(api.login).mockRejectedValue({ code });
    render(<App />);
    await screen.findByRole("button", { name: game.title });
    fireEvent.click(screen.getByRole("button", { name: "连接 Epic Games" }));
    fireEvent.change(screen.getByLabelText("授权码"), {
      target: { value: "temporary-code" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登录并同步游戏库" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
    expect(screen.getByLabelText("授权码")).toBeTruthy();
    expect(api.library).not.toHaveBeenCalled();
  });

  it("keeps the launch dialog open and displays actionable failures", async () => {
    vi.mocked(api.launch).mockRejectedValue({ code: "UPDATE_REQUIRED" });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: game.title }));
    fireEvent.click(screen.getByRole("button", { name: "启动游戏" }));
    const error = await screen.findByRole("alert");
    expect(error.textContent).toContain("更新");
    expect(screen.getByRole("button", { name: "启动游戏" })).toBeTruthy();
  });

  it("retains installed games when the remote refresh fails", async () => {
    vi.mocked(api.status).mockResolvedValue({
      hasCredentials: true,
      displayName: "Player",
      defaultPlatform: "Windows",
    });
    vi.mocked(api.library).mockRejectedValue({ code: "NETWORK_ERROR" });
    render(<App />);
    expect(
      await screen.findByRole("button", { name: game.title }),
    ).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toContain("网络");
  });
});

const installTask: InstallTask = {
  operation: "install",
  id: "test-task",
  appName: "remote-game",
  title: "Remote Game",
  state: "ready",
  progress: 0,
  downloadSpeed: 0,
  downloadedBytes: 0,
  etaSeconds: null,
  error: null,
  plan: {
    installPath: "/games/remote",
    platform: "Windows",
    version: "2",
    downloadSize: 1024 ** 2,
    installSize: 2 * 1024 ** 2,
    requiredSpace: 2 * 1024 ** 2,
    freeSpace: 10 * 1024 ** 3,
    warnings: [],
    failures: [],
    updating: false,
  },
};

async function openInstall(importing = false) {
  vi.mocked(api.status).mockResolvedValue({
    hasCredentials: true,
    displayName: "Player",
    defaultPlatform: "Windows",
  });
  vi.mocked(api.library).mockResolvedValue({
    games: [
      {
        appName: "remote-game",
        title: "Remote Game",
        version: "2",
        platform: "Windows",
      },
    ],
  });
  vi.mocked(api.installOptions).mockResolvedValue({
    installPath: "/games/remote",
    updating: false,
    platform: "Windows",
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Remote Game" }));
  fireEvent.click(screen.getByRole("button", { name: importing ? "导入已有游戏" : "安装游戏" }));
  await waitFor(() =>
    expect(
      (screen.getByLabelText(importing ? "已有游戏目录" : "游戏安装目录") as HTMLInputElement).disabled,
    ).toBe(false),
  );
}

const importTask: InstallTask = {
  ...installTask,
  operation: "import",
  progress: 100,
  plan: {
    ...installTask.plan!,
    downloadSize: 0,
    requiredSpace: 0,
    freeSpace: null,
    importSummary: { totalFiles: 3, matchedFiles: 3, missingFiles: 0, changedFiles: 0, problemFiles: [] },
  },
};

describe("import existing games", () => {
  it("checks a selected directory before confirming import and refreshing installed games", async () => {
    vi.mocked(api.chooseInstallDirectory).mockResolvedValue("/games/remote");
    vi.mocked(api.prepareImport).mockImplementation(async () => {
      vi.mocked(api.installStatus).mockResolvedValue({ task: importTask });
      return { task: importTask };
    });
    vi.mocked(api.startInstall).mockImplementation(async () => {
      const done = { ...importTask, state: "completed" as const };
      vi.mocked(api.installStatus).mockResolvedValue({ task: done });
      return { task: done };
    });
    await openInstall(true);
    expect((screen.getByLabelText("已有游戏目录") as HTMLInputElement).value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "选择目录" }));
    await waitFor(() => expect((screen.getByLabelText("已有游戏目录") as HTMLInputElement).value).toBe("/games/remote"));
    fireEvent.click(screen.getByRole("button", { name: "检查已有文件" }));
    await screen.findByRole("heading", { name: "确认导入 Remote Game" });
    expect(api.prepareImport).toHaveBeenCalledWith("remote-game", "Windows", "/games/remote");
    expect(api.prepareInstall).not.toHaveBeenCalled();
    expect(api.startInstall).not.toHaveBeenCalled();
    expect(screen.getByText("3 / 3 个文件")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    await screen.findByText("导入完成");
    expect(api.installed).toHaveBeenCalledTimes(2);
  });

  it("blocks incomplete games and allows cancelling the import plan", async () => {
    const blocked = {
      ...importTask,
      plan: { ...importTask.plan!, failures: ["文件不完整"], importSummary: {
        totalFiles: 3, matchedFiles: 1, missingFiles: 1, changedFiles: 1, problemFiles: ["game.exe"],
      } },
    };
    vi.mocked(api.prepareImport).mockResolvedValue({ task: blocked });
    vi.mocked(api.cancelInstall).mockResolvedValue({ task: { ...blocked, state: "cancelled" } });
    await openInstall(true);
    fireEvent.change(screen.getByLabelText("已有游戏目录"), { target: { value: "/games/remote" } });
    fireEvent.click(screen.getByRole("button", { name: "检查已有文件" }));
    await screen.findByText("文件不完整");
    expect((screen.getByRole("button", { name: "确认导入" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("game.exe")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "取消此计划" }));
    await screen.findByText("导入已取消，原有游戏文件与安装记录已保留。");
    expect(api.startInstall).not.toHaveBeenCalled();
  });

  it("allows choosing another directory for a recorded installation", async () => {
    vi.mocked(api.status).mockResolvedValue({ hasCredentials: true, displayName: "Player", defaultPlatform: "Windows" });
    vi.mocked(api.installOptions).mockResolvedValue({ installPath: game.installPath, updating: true, platform: "Windows" });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: game.title }));
    fireEvent.click(screen.getByRole("button", { name: "重新关联目录" }));
    await waitFor(() => expect((screen.getByLabelText("已有游戏目录") as HTMLInputElement).value).toBe(game.installPath));
    expect((screen.getByLabelText("已有游戏目录") as HTMLInputElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "选择目录" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("desktop installation", () => {
  it("requires a reviewed plan before downloading and refreshes installations after completion", async () => {
    vi.mocked(api.prepareInstall).mockImplementation(async () => {
      vi.mocked(api.installStatus).mockResolvedValue({ task: installTask });
      return { task: installTask };
    });
    vi.mocked(api.startInstall).mockImplementation(async () => {
      const done = {
        ...installTask,
        state: "completed" as const,
        progress: 100,
      };
      vi.mocked(api.installStatus).mockResolvedValue({ task: done });
      return { task: { ...installTask, state: "downloading" } };
    });
    await openInstall();
    expect(api.startInstall).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "检查安装" }));
    await screen.findByRole("heading", { name: "确认安装 Remote Game" });
    expect(api.prepareInstall).toHaveBeenCalledWith(
      "remote-game",
      "Windows",
      "/games/remote",
    );
    expect(screen.getByText("下载大小")).toBeTruthy();
    expect(api.startInstall).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认并开始安装" }));
    await waitFor(() =>
      expect(api.startInstall).toHaveBeenCalledWith("test-task"),
    );
    await screen.findByText("安装完成");
    expect(api.installed).toHaveBeenCalledTimes(2);
  });

  it("blocks installation when the plan reports insufficient space", async () => {
    const blocked = {
      ...installTask,
      plan: { ...installTask.plan!, failures: ["Not enough disk space"] },
    };
    vi.mocked(api.prepareInstall).mockResolvedValue({ task: blocked });
    await openInstall();
    fireEvent.click(screen.getByRole("button", { name: "检查安装" }));
    await screen.findByText("Not enough disk space");
    expect(
      (
        screen.getByRole("button", {
          name: "确认并开始安装",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(api.startInstall).not.toHaveBeenCalled();
  });

  it("uses the native directory selection for the installation path", async () => {
    vi.mocked(api.chooseInstallDirectory).mockResolvedValue(
      "/new/games/Remote",
    );
    await openInstall();
    fireEvent.click(screen.getByRole("button", { name: "选择目录" }));
    await waitFor(() =>
      expect(
        (screen.getByLabelText("游戏安装目录") as HTMLInputElement).value,
      ).toBe("/new/games/Remote"),
    );
  });
});
