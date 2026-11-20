import { invoke, isTauri } from "@tauri-apps/api/core";

export interface AuthStatus {
  hasCredentials: boolean;
  displayName: string | null;
  defaultPlatform: string;
}

export interface LibraryGame {
  appName: string;
  title: string;
  version: string | null;
  platform: string;
  coverUrl?: string | null;
}

export interface InstalledGame extends LibraryGame {
  installPath: string;
  installSize: number;
  canRunOffline: boolean;
  needsVerification: boolean;
  executable: boolean;
}

export interface BackendError {
  code: string;
  message: string;
}

export interface InstallPlan {
  installPath: string;
  platform: string;
  version: string;
  downloadSize: number;
  installSize: number;
  requiredSpace: number;
  freeSpace: number | null;
  warnings: string[];
  failures: string[];
  updating: boolean;
  previousPath?: string | null;
  importSummary?: {
    totalFiles: number;
    matchedFiles: number;
    missingFiles: number;
    changedFiles: number;
    problemFiles: string[];
  };
}

export interface InstallTask {
  id: string;
  appName: string;
  title: string;
  operation: "install" | "import";
  state:
    | "preparing"
    | "ready"
    | "downloading"
    | "verifying"
    | "cancelling"
    | "cancelled"
    | "completed"
    | "failed";
  progress: number;
  downloadSpeed: number;
  downloadedBytes: number;
  etaSeconds: number | null;
  plan: InstallPlan | null;
  error: BackendError | null;
  prerequisite?: string | null;
}

export const desktopAvailable = isTauri();

async function call<T>(
  command: string,
  args?: Record<string, unknown>,
): Promise<T> {
  if (!desktopAvailable) {
    throw {
      code: "DESKTOP_REQUIRED",
      message: "请在 Legendary 桌面应用中连接游戏库。",
    };
  }
  return invoke<T>(command, args);
}

export const api = {
  status: () => call<AuthStatus>("auth_status"),
  login: (code: string) => call<AuthStatus>("auth_login", { code }),
  logout: () => call<AuthStatus>("auth_logout"),
  installed: () => call<{ games: InstalledGame[] }>("installed_games"),
  library: (platform: string, forceRefresh = false) =>
    call<{ games: LibraryGame[] }>("list_games", { platform, forceRefresh }),
  launch: (appName: string, offline: boolean, skipVersionCheck: boolean) =>
    call<{ appName: string; pid: number }>("launch_game", {
      appName,
      offline,
      skipVersionCheck,
    }),
  openLogin: () => call<void>("open_login"),
  installOptions: (appName: string, platform: string) =>
    call<{ installPath: string; platform: string; updating: boolean }>(
      "install_options",
      { appName, platform },
    ),
  prepareInstall: (appName: string, platform: string, installPath: string) =>
    call<{ task: InstallTask }>("prepare_install", {
      appName,
      platform,
      installPath,
    }),
  prepareImport: (appName: string, platform: string, installPath: string) =>
    call<{ task: InstallTask }>("prepare_import", {
      appName,
      platform,
      installPath,
    }),
  installStatus: () => call<{ task: InstallTask | null }>("install_status"),
  startInstall: (taskId: string) =>
    call<{ task: InstallTask }>("start_install", { taskId }),
  cancelInstall: (taskId: string) =>
    call<{ task: InstallTask }>("cancel_install", { taskId }),
  chooseInstallDirectory: () => call<string | null>("choose_install_directory"),
};

const messages: Record<string, string> = {
  INVALID_PARAMS: "输入内容无效，请检查后重试。",
  AUTH_REQUIRED: "请先登录 Epic Games 账户，或重新登录以更新凭据。",
  AUTH_CODE_INVALID:
    "Epic 已拒绝此授权码，可能已过期或已被使用，请重新获取后再试。",
  AUTH_FAILED: "Epic Games 拒绝了登录请求，请确认账户状态后重试。",
  AUTH_ACTION_REQUIRED:
    "请先在 Epic Games 官网完成账户验证或协议确认，然后重新获取授权码。",
  LOGIN_FAILED: "登录失败，请检查网络连接，或重新登录。",
  NETWORK_ERROR: "暂时无法连接 Epic Games，请检查网络后重试。",
  TLS_ERROR:
    "无法验证与 Epic Games 的安全连接，请检查系统时间、证书和代理设置。",
  CONFIG_WRITE_ERROR:
    "无法读取或保存登录凭据，请检查 Legendary 配置目录的访问权限。",
  GAME_NOT_FOUND: "找不到这个游戏，请刷新游戏库后重试。",
  INSTALL_BUSY: "已有安装或导入任务，请先完成或取消当前任务。",
  INSTALL_LOCKED: "另一个 Legendary 程序正在修改安装记录，请等待它完成后重试。",
  INSTALL_UNSUPPORTED: "此内容需要第三方启动器或游戏本体，请通过 CLI 管理。",
  INSTALL_PLATFORM_UNAVAILABLE: "这个游戏不支持所选平台，请切换平台后重试。",
  INSTALL_PATH_INVALID:
    "请选择有效的游戏专用绝对路径；更新时请使用原安装目录。",
  INSTALL_DIRECTORY_NOT_EMPTY:
    "此目录已有其他文件，请选择空目录或此前下载使用的目录。",
  INSTALL_PERMISSION_DENIED: "无法写入安装目录或配置目录，请检查访问权限。",
  INSTALL_MEMORY_REQUIRED:
    "下载所需的共享内存不足，请关闭其他任务或通过 CLI 调整内存配置。",
  INSTALL_REQUIREMENTS: "安装条件未满足，请检查磁盘空间和安装目录。",
  INSTALL_PLAN_EXPIRED: "安装计划已过期，请重新检查安装。",
  INSTALL_NOT_READY: "请等待安装分析完成后再开始下载。",
  INSTALL_TASK_NOT_FOUND: "找不到此安装任务，请重新检查安装。",
  INSTALL_DOWNLOAD_FAILED:
    "下载或文件写入失败。请检查网络和磁盘，重试可续传已完成文件。",
  INSTALL_VERIFY_FAILED: "游戏文件校验失败，尚未登记为安装成功，请重试。",
  INSTALL_WORKER_EXITED: "安装进程意外退出，请重新打开安装并续传。",
  INSTALL_FAILED: "安装失败，请检查网络、磁盘空间和目录权限后重试。",
  INSTALL_MANIFEST_INVALID: "游戏清单包含无效路径，无法继续，请刷新游戏库后重试。",
  IMPORT_PATH_MISSING: "找不到此游戏目录，请选择已有游戏的根目录，并检查磁盘是否已连接。",
  IMPORT_DIRECTORY_IN_USE: "此目录已关联到其他游戏，请选择当前游戏的专用目录。",
  IMPORT_PLATFORM_CONFLICT: "重新关联目录时，请使用原安装记录的平台。",
  IMPORT_MANIFEST_INVALID: "游戏清单无效或与所选游戏不匹配，请确认目录和平台是否正确。",
  IMPORT_INCOMPLETE: "文件不完整或校验未通过，尚未导入。请检查目录，或通过 CLI 导入后修复。",
  IMPORT_FILES_CHANGED: "校验后游戏文件发生了变化，尚未导入，请重新检查已有文件。",
  IMPORT_PERMISSION_DENIED: "无法读取游戏文件或保存安装记录，请检查目录权限。",
  IMPORT_FAILED: "导入失败，请检查游戏目录、网络和访问权限后重试。",
  NOT_INSTALLED: "这个游戏还没有安装。",
  NOT_LAUNCHABLE: "这个内容没有独立的启动程序，请启动本体游戏。",
  INSTALL_MISSING: "找不到游戏安装目录，请检查磁盘是否已连接。",
  UPDATE_REQUIRED:
    "游戏有可用更新，请先更新游戏，或在启动选项中跳过版本检查。",
  VERIFICATION_REQUIRED:
    "安装尚未完成或需要校验。请先续传安装，或通过 CLI 校验游戏文件。",
  METADATA_MISSING: "缺少游戏信息，请先刷新游戏库。",
  BACKEND_UNAVAILABLE: "游戏服务未能启动，请检查桌面安装或重新打开应用。",
  BACKEND_CACHE_ERROR: "无法释放或校验内置游戏服务，请检查应用缓存目录的可用空间和访问权限。",
  BACKEND_EXITED: "游戏服务已断开，请重试以重新连接。",
  BACKEND_TIMEOUT: "请求超时，请检查网络并重试。",
  BACKEND_BUSY: "游戏服务正在处理其他请求，请稍后重试。",
  PROTOCOL_MISMATCH: "桌面应用与游戏服务版本不兼容，请重新安装完整应用。",
  INTERNAL_ERROR: "操作未能完成，请重试。",
};

export function errorMessage(error: unknown): string {
  if (typeof error === "object" && error && "code" in error) {
    const typed = error as BackendError;
    return messages[typed.code] ?? typed.message ?? "操作失败，请重试。";
  }
  return "操作失败，请重试。";
}
