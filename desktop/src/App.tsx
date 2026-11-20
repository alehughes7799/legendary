import { useEffect, useState, type FormEvent } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Check,
  ChevronDown,
  Crown,
  ExternalLink,
  FolderOpen,
  Gamepad2,
  HardDrive,
  Library,
  LoaderCircle,
  LogIn,
  LogOut,
  Play,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  WifiOff,
  X,
} from "lucide-react";
import {
  api,
  desktopAvailable,
  errorMessage,
  type AuthStatus,
  type InstalledGame,
  type LibraryGame,
} from "./api";
import { formatSize, mergeLibrary, type Game } from "./library";
import Modal from "./Modal";
import DownloadsPage from "./DownloadsPage";

type Page = "library" | "installed" | "downloads" | "settings";
const initialAuth: AuthStatus = {
  hasCredentials: false,
  displayName: null,
  defaultPlatform: "Windows",
};

function Cover({ game, large = false }: { game: Game; large?: boolean }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className={`cover ${large ? "cover-large" : ""}`}>
      {game.coverUrl && !failed ? (
        <img
          src={game.coverUrl}
          alt=""
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="cover-fallback">
          <Gamepad2 size={large ? 48 : 38} strokeWidth={1} />
          <span>{game.title}</span>
        </div>
      )}
      <span className="cover-brand">LEGENDARY</span>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("library");
  const [auth, setAuth] = useState(initialAuth);
  const [library, setLibrary] = useState<LibraryGame[]>([]);
  const [installed, setInstalled] = useState<InstalledGame[]>([]);
  const [query, setQuery] = useState("");
  const [platform, setPlatform] = useState("Windows");
  const [busy, setBusy] = useState<string | null>(
    desktopAvailable ? "正在连接游戏库" : null,
  );
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loginOpen, setLoginOpen] = useState(false);
  const [code, setCode] = useState("");
  const [installGame, setInstallGame] = useState<Game | null>(null);
  const [installOperation, setInstallOperation] = useState<
    "install" | "import"
  >("install");
  const [installationActive, setInstallationActive] = useState(false);
  const [selected, setSelected] = useState<Game | null>(null);
  const [offline, setOffline] = useState(false);
  const [skipVersionCheck, setSkipVersionCheck] = useState(false);

  const games = mergeLibrary(library, installed);
  const visible = games.filter(
    (game) =>
      (page !== "installed" || game.installation) &&
      `${game.title} ${game.appName}`
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
  );

  async function refreshRemote(targetPlatform: string, force = false) {
    const result = await api.library(targetPlatform, force);
    setLibrary(result.games);
  }

  async function connect() {
    setBusy("正在连接游戏库");
    setError(null);
    try {
      const [status, local] = await Promise.all([
        api.status(),
        api.installed(),
      ]);
      setAuth(status);
      setPlatform(status.defaultPlatform);
      setInstalled(local.games);
      if (status.hasCredentials) {
        setBusy("正在同步 Epic 游戏库");
        await refreshRemote(status.defaultPlatform);
      }
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    if (desktopAvailable) void connect();
  }, []);

  async function refresh(targetPlatform = platform, force = false) {
    setBusy("正在刷新游戏库");
    setError(null);
    setNotice(null);
    try {
      setInstalled((await api.installed()).games);
      if (auth.hasCredentials) await refreshRemote(targetPlatform, force);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function login(event: FormEvent) {
    event.preventDefault();
    if (!code.trim() || busy) return;
    setBusy("正在登录");
    setError(null);
    try {
      const status = await api.login(code.trim());
      setAuth(status);
      setPlatform(status.defaultPlatform);
      setCode("");
      setLoginOpen(false);
      setBusy("正在同步 Epic 游戏库");
      await refreshRemote(status.defaultPlatform);
      setNotice("登录成功，游戏库已同步。");
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function logout() {
    setBusy("正在退出登录");
    setError(null);
    setNotice(null);
    try {
      setAuth(await api.logout());
      setLibrary([]);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function launch(game: Game) {
    setBusy(`正在启动 ${game.title}`);
    setError(null);
    setNotice(null);
    try {
      await api.launch(game.appName, offline, skipVersionCheck);
      setNotice(`已发送 ${game.title} 的启动请求。`);
      setSelected(null);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  function openGame(game: Game) {
    setSelected(game);
    setOffline(false);
    setSkipVersionCheck(false);
  }

  function openInstallation(game: Game, operation: "install" | "import") {
    setInstallOperation(operation);
    setInstallGame(game);
    setSelected(null);
    setPage("downloads");
  }
  async function openLoginPage() {
    try {
      await api.openLogin();
    } catch (error) {
      setError(errorMessage(error));
    }
  }

  const feedback = (
    <>
      {error && (
        <div className="feedback error" role="alert">
          <span>{error}</span>
          <button
            className="icon-button"
            onClick={() => setError(null)}
            aria-label="关闭错误"
          >
            <X size={16} />
          </button>
        </div>
      )}
      {notice && (
        <div className="feedback success" role="status">
          <Check size={17} />
          <span>{notice}</span>
          <button
            className="icon-button"
            onClick={() => setNotice(null)}
            aria-label="关闭提示"
          >
            <X size={16} />
          </button>
        </div>
      )}
    </>
  );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          aria-label="Legendary 游戏库"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            setPage("library");
          }}
        >
          <span className="brand-mark">
            <Crown size={23} strokeWidth={1.7} />
          </span>
          <span>
            LEGENDARY<small>YOUR GAMES. YOUR WAY.</small>
          </span>
        </a>
        <div className="nav-label">工作空间</div>
        <nav aria-label="主导航">
          <button
            className={page === "library" ? "nav-item active" : "nav-item"}
            onClick={() => setPage("library")}
          >
            <Library size={19} />
            游戏库<span className="nav-count">{games.length}</span>
          </button>
          <button
            className={page === "installed" ? "nav-item active" : "nav-item"}
            onClick={() => setPage("installed")}
          >
            <HardDrive size={19} />
            已安装<span className="nav-count">{installed.length}</span>
          </button>
          <button
            className={page === "downloads" ? "nav-item active" : "nav-item"}
            aria-current={page === "downloads" ? "page" : undefined}
            onClick={() => setPage("downloads")}
          >
            <ArrowDownToLine size={19} />
            下载管理
            {installationActive && (
              <span className="nav-count" aria-label="1 个进行中的任务">
                1
              </span>
            )}
          </button>
          <button
            className={page === "settings" ? "nav-item active" : "nav-item"}
            onClick={() => setPage("settings")}
          >
            <Settings2 size={19} />
            设置
          </button>
        </nav>
        <div className="sidebar-bottom">
          <div className="local-note">
            <ShieldCheck size={19} />
            <div>
              熟悉的游戏，自由的体验<small>Powered by Legendary</small>
            </div>
          </div>
          <button
            className="account"
            disabled={!!busy || !desktopAvailable}
            onClick={() =>
              auth.hasCredentials ? setPage("settings") : setLoginOpen(true)
            }
          >
            <span className="avatar">
              {auth.displayName?.slice(0, 1).toUpperCase() || (
                <Gamepad2 size={19} />
              )}
            </span>
            <span>
              {auth.displayName || "连接你的账户"}
              <small>
                {auth.hasCredentials ? "Epic Games" : "登录以同步游戏库"}
              </small>
            </span>
            <ArrowRight size={15} />
          </button>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <span>你的下一段冒险，从这里开始。</span>
          <div className="connection">
            <span
              className={desktopAvailable ? "status-dot" : "status-dot preview"}
            />
            {desktopAvailable ? "桌面版 · 预览" : "浏览器预览"}
          </div>
        </header>
        <div className="content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                {page === "settings"
                  ? "MAKE IT YOURS"
                  : page === "downloads"
                    ? "READY FOR YOUR NEXT ADVENTURE"
                    : "ALL YOUR ADVENTURES"}
              </div>
              <h1>
                {page === "library"
                  ? "游戏库"
                  : page === "installed"
                    ? "已安装的游戏"
                    : page === "downloads"
                      ? "下载管理"
                      : "设置"}
              </h1>
              <p>
                {page === "settings"
                  ? "管理账户与启动方式。"
                  : page === "installed"
                    ? "准备就绪，随时出发。"
                    : page === "downloads"
                      ? "管理游戏的下载、安装、更新与导入。"
                      : "把喜爱的世界，收藏在同一个地方。"}
              </p>
            </div>
            {page === "downloads" && (
              <button
                className="button secondary"
                onClick={() => setPage("library")}
              >
                <Library size={16} />
                返回游戏库
              </button>
            )}
            {(page === "library" || page === "installed") && (
              <button
                className="button secondary"
                onClick={() => void refresh(platform, true)}
                disabled={!!busy || installationActive || !desktopAvailable}
              >
                <RefreshCw size={16} className={busy ? "spin" : ""} />
                刷新游戏库
              </button>
            )}
          </div>

          {!loginOpen && !selected && feedback}
          {!desktopAvailable && (
            <div className="preview-note">
              <Gamepad2 size={19} />
              <span>
                界面预览已就绪。通过桌面应用打开后，即可登录并读取真实游戏库。
              </span>
            </div>
          )}

          <DownloadsPage
            visible={page === "downloads"}
            game={installGame}
            operation={installOperation}
            onClose={() => setInstallGame(null)}
            onBrowseLibrary={() => setPage("library")}
            onActiveChange={setInstallationActive}
            onInstalled={() => {
              void api
                .installed()
                .then((local) => setInstalled(local.games))
                .catch((error) => setError(errorMessage(error)));
            }}
          />

          {page === "downloads" ? null : page === "settings" ? (
            <div className="settings-grid">
              <section className="panel">
                <div className="panel-icon">
                  <Gamepad2 size={23} />
                </div>
                <h2>Epic Games 账户</h2>
                <p>
                  {auth.hasCredentials
                    ? `当前账户：${auth.displayName || "Epic Games 用户"}`
                    : "连接账户后，同步你拥有的游戏。"}
                </p>
                {auth.hasCredentials ? (
                  <button
                    className="button secondary"
                    disabled={!!busy || installationActive}
                    onClick={() => void logout()}
                  >
                    <LogOut size={16} />
                    退出登录
                  </button>
                ) : (
                  <button
                    className="button primary"
                    disabled={!!busy || !desktopAvailable}
                    onClick={() => setLoginOpen(true)}
                  >
                    <LogIn size={16} />
                    登录 Epic Games
                  </button>
                )}
              </section>
              <section className="panel">
                <div className="panel-icon">
                  <Settings2 size={23} />
                </div>
                <h2>游戏启动设置</h2>
                <p>
                  启动时可选择离线模式与版本检查。Wine、CrossOver
                  和游戏参数会沿用现有 Legendary 配置。
                </p>
                <div className="muted-detail">
                  选择游戏即可安装或更新，云存档和修复仍通过 CLI 管理。
                </div>
              </section>
            </div>
          ) : (
            <>
              {!auth.hasCredentials && (
                <section className="welcome-banner">
                  <div className="welcome-copy">
                    <span className="tag">
                      <span />
                      PLAY ON YOUR TERMS
                    </span>
                    <h2>
                      属于你的游戏，
                      <br />
                      由你掌控。
                    </h2>
                    <p>连接 Epic Games，找回收藏的每一个世界。</p>
                    <button
                      className="button light"
                      disabled={!!busy || !desktopAvailable}
                      onClick={() => setLoginOpen(true)}
                    >
                      连接 Epic Games
                      <ArrowRight size={17} />
                    </button>
                  </div>
                  <div className="hero-art" aria-hidden="true">
                    <div className="orbit orbit-one" />
                    <div className="orbit orbit-two" />
                    <div className="hero-crown">
                      <Crown size={92} strokeWidth={1} />
                    </div>
                    <span className="art-star star-one">✦</span>
                    <span className="art-star star-two">✧</span>
                    <span className="art-star star-three">+</span>
                  </div>
                </section>
              )}

              <div className="library-toolbar">
                <div className="collection-title">
                  {page === "installed" ? "本地收藏" : "全部游戏"}
                  <span>
                    {page === "installed" ? installed.length : games.length}
                  </span>
                </div>
                <div className="toolbar-controls">
                  <label className="search">
                    <Search size={17} />
                    <input
                      aria-label="搜索游戏"
                      placeholder="搜索游戏…"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                    />
                    {query && (
                      <button
                        className="icon-button"
                        onClick={() => setQuery("")}
                        aria-label="清空搜索"
                      >
                        <X size={15} />
                      </button>
                    )}
                  </label>
                  <label className="platform-select">
                    <select
                      aria-label="游戏平台"
                      value={platform}
                      disabled={!!busy || installationActive}
                      onChange={(event) => {
                        setPlatform(event.target.value);
                        void refresh(event.target.value);
                      }}
                    >
                      <option value="Windows">Windows</option>
                      <option value="Mac">macOS</option>
                    </select>
                    <ChevronDown size={14} />
                  </label>
                </div>
              </div>

              {busy && (
                <div className="loading-line" role="status">
                  <LoaderCircle className="spin" size={16} />
                  {busy}…
                </div>
              )}
              {visible.length > 0 ? (
                <div className="game-grid">
                  {visible.map((game) => (
                    <article key={game.appName} className="game-card">
                      <button
                        className="game-cover-button"
                        onClick={() => openGame(game)}
                        aria-label={`查看 ${game.title}`}
                      >
                        <Cover game={game} />
                        {game.installation && (
                          <span className="installed-tag">
                            <Check size={11} />
                            已安装
                          </span>
                        )}
                      </button>
                      <button
                        className="game-title"
                        title={game.title}
                        onClick={() => openGame(game)}
                      >
                        {game.title}
                      </button>
                      <div className="game-meta">
                        <span>
                          {game.installation
                            ? formatSize(game.installation.installSize)
                            : game.platform === "Mac"
                              ? "macOS"
                              : "Windows"}
                        </span>
                        <button
                          className="card-action"
                          disabled={!!busy}
                          onClick={() => openGame(game)}
                          aria-label={
                            game.installation
                              ? `启动 ${game.title}`
                              : `查看 ${game.title}`
                          }
                        >
                          {game.installation ? (
                            <Play size={15} fill="currentColor" />
                          ) : (
                            <ArrowRight size={16} />
                          )}
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                !busy && (
                  <div className="empty-state">
                    {query ? (
                      <Search size={33} strokeWidth={1.2} />
                    ) : (
                      <Library size={35} strokeWidth={1.2} />
                    )}
                    <h3>
                      {query
                        ? "没有找到匹配的游戏"
                        : page === "installed"
                          ? "还没有已安装的游戏"
                          : auth.hasCredentials
                            ? "这里等待着新的冒险"
                            : "你的收藏，即将在这里相遇"}
                    </h3>
                    <p>
                      {query
                        ? "试试游戏名称或 App Name。"
                        : auth.hasCredentials
                          ? "在游戏库中选择游戏，即可安装或导入已有目录。"
                          : "连接账户以查看游戏，本地安装记录也会自动显示。"}
                    </p>
                    {error && desktopAvailable && (
                      <button
                        className="button secondary"
                        onClick={() => void connect()}
                      >
                        重新连接
                      </button>
                    )}
                  </div>
                )
              )}
            </>
          )}
          <footer>
            <span>LEGENDARY DESKTOP</span>
            <span>自由探索 · 尽情游玩</span>
          </footer>
        </div>
      </main>

      {loginOpen && (
        <Modal
          title="连接 Epic Games"
          onClose={() => {
            if (!busy) {
              setLoginOpen(false);
              setCode("");
            }
          }}
        >
          {feedback}
          <p className="modal-description">
            在浏览器中登录 Epic Games，将返回的 <code>authorizationCode</code>{" "}
            或完整 JSON 粘贴到下方。
          </p>
          <button
            className="button secondary full-width"
            onClick={() => void openLoginPage()}
            disabled={!!busy}
          >
            <ExternalLink size={16} />
            打开 Epic 登录页面
          </button>
          <form onSubmit={(event) => void login(event)}>
            <label className="field-label" htmlFor="auth-code">
              授权码
            </label>
            <textarea
              autoFocus
              id="auth-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder="粘贴 authorizationCode 或 JSON…"
              rows={4}
              autoComplete="off"
              spellCheck={false}
              disabled={!!busy}
            />
            <p className="field-hint">
              授权码只用于登录。登录凭据由本地 Legendary 管理。
            </p>
            <button
              type="submit"
              className="button primary full-width"
              disabled={!!busy || !code.trim()}
            >
              {busy ? (
                <LoaderCircle className="spin" size={17} />
              ) : (
                <LogIn size={17} />
              )}
              {busy || "登录并同步游戏库"}
            </button>
          </form>
        </Modal>
      )}

      {selected && (
        <Modal
          title={selected.title}
          onClose={() => {
            if (!busy) setSelected(null);
          }}
        >
          {feedback}
          <div className="game-detail">
            <Cover game={selected} large />
            <div>
              <span className="detail-label">
                {selected.installation ? "已安装" : "游戏库"}
              </span>
              <h3>{selected.title}</h3>
              <p>
                {(selected.installation?.platform ?? selected.platform) ===
                "Mac"
                  ? "macOS"
                  : "Windows"}
              </p>
              {selected.installation && (
                <p>{formatSize(selected.installation.installSize)}</p>
              )}
            </div>
          </div>
          {selected.installation ? (
            <>
              <div className="install-location">
                <span>安装位置</span>
                <code>{selected.installation.installPath}</code>
              </div>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={offline}
                  disabled={!!busy}
                  onChange={(event) => setOffline(event.target.checked)}
                />
                <span>
                  <WifiOff size={16} />
                  离线启动
                </span>
              </label>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={skipVersionCheck}
                  disabled={!!busy || offline}
                  onChange={(event) =>
                    setSkipVersionCheck(event.target.checked)
                  }
                />
                <span>跳过版本检查</span>
              </label>
              <button
                className="button primary full-width"
                disabled={!!busy || !selected.installation.executable}
                onClick={() => void launch(selected)}
              >
                {busy ? (
                  <LoaderCircle size={17} className="spin" />
                ) : (
                  <Play size={17} fill="currentColor" />
                )}
                {busy || "启动游戏"}
              </button>
              <button
                className="button secondary full-width update-button"
                disabled={!!busy || installationActive || !auth.hasCredentials}
                onClick={() => openInstallation(selected, "install")}
              >
                <ArrowDownToLine size={17} />
                检查更新 / 续传
              </button>
            </>
          ) : (
            <button
              className="button primary full-width"
              disabled={!!busy || installationActive || !auth.hasCredentials}
              onClick={() => openInstallation(selected, "install")}
            >
              <ArrowDownToLine size={18} />
              安装游戏
            </button>
          )}
          <button
            className="button secondary full-width update-button"
            disabled={!!busy || installationActive || !auth.hasCredentials}
            onClick={() => openInstallation(selected, "import")}
          >
            <FolderOpen size={17} />
            {selected.installation ? "重新关联目录" : "导入已有游戏"}
          </button>
        </Modal>
      )}
    </div>
  );
}
