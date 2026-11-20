import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowDownToLine,
  Check,
  Clock3,
  FolderOpen,
  Library,
  LoaderCircle,
  X,
} from "lucide-react";
import { api, desktopAvailable, errorMessage, type InstallTask } from "./api";
import type { Game } from "./library";

export function bytes(value: number): string {
  if (value < 1024) return `${Math.round(value)} B`;
  const unit = Math.min(3, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** unit).toFixed(1)} ${["B", "KB", "MB", "GB"][unit]}`;
}

const labels: Record<InstallTask["state"], string> = {
  preparing: "正在分析安装",
  ready: "等待确认",
  downloading: "正在下载",
  verifying: "正在校验文件",
  cancelling: "正在取消",
  cancelled: "已取消",
  completed: "安装完成",
  failed: "安装失败",
};
const terminal = (task: InstallTask) =>
  ["completed", "cancelled", "failed"].includes(task.state);

export default function DownloadsPage({
  visible,
  game,
  operation,
  onClose,
  onInstalled,
  onBrowseLibrary,
  onActiveChange,
}: {
  visible: boolean;
  game: Game | null;
  operation: "install" | "import";
  onClose: () => void;
  onInstalled: () => void;
  onBrowseLibrary: () => void;
  onActiveChange: (active: boolean) => void;
}) {
  const [task, setTask] = useState<InstallTask | null>(null);
  const [path, setPath] = useState("");
  const [platform, setPlatform] = useState("Windows");
  const [updating, setUpdating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const completed = useRef<string | null>(null);
  const onInstalledRef = useRef(onInstalled);
  onInstalledRef.current = onInstalled;

  useEffect(() => {
    onActiveChange(!!task && !terminal(task));
  }, [task?.state, onActiveChange]);

  function accept(next: InstallTask | null) {
    setTask(next);
    if (next?.state === "completed" && completed.current !== next.id) {
      completed.current = next.id;
      onInstalledRef.current();
    }
  }

  useEffect(() => {
    if (!desktopAvailable) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api.installStatus();
        if (!stopped) accept(result.task);
      } catch (error) {
        if (!stopped) setError(errorMessage(error));
      }
      if (!stopped) timer = setTimeout(() => void poll(), 1000);
    }
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!game) return;
    let stopped = false;
    setPath("");
    setError(null);
    setBusy(true);
    api
      .installOptions(
        game.appName,
        game.installation?.platform ?? game.platform,
      )
      .then((options) => {
        if (!stopped) {
          setPath(
            operation === "import" && !options.updating
              ? ""
              : options.installPath,
          );
          setPlatform(options.platform);
          setUpdating(options.updating);
        }
      })
      .catch((error) => {
        if (!stopped) setError(errorMessage(error));
      })
      .finally(() => {
        if (!stopped) setBusy(false);
      });
    return () => {
      stopped = true;
    };
  }, [game, operation]);

  async function prepare(event: FormEvent) {
    event.preventDefault();
    if (!game || busy) return;
    setBusy(true);
    setError(null);
    try {
      accept(
        (
          await (
            operation === "import" ? api.prepareImport : api.prepareInstall
          )(game.appName, platform, path.trim())
        ).task,
      );
      onClose();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function command(action: "start" | "cancel") {
    if (!task || busy) return;
    setBusy(true);
    setError(null);
    try {
      accept(
        (
          await (action === "start"
            ? api.startInstall(task.id)
            : api.cancelInstall(task.id))
        ).task,
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function chooseDirectory() {
    setBusy(true);
    try {
      const selected = await api.chooseInstallDirectory();
      if (selected) setPath(selected);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  // Keep polling when browsing other pages, without rendering task controls there.
  if (!visible) return null;

  const failure =
    error ||
    (!game && task?.state === "failed" && task.error
      ? errorMessage(task.error)
      : null);
  const feedback = failure && (
    <div role="alert" className="feedback error">
      {failure}
    </div>
  );
  const importing = operation === "import";
  const taskImporting = task?.operation === "import";
  const taskLabel = taskImporting
    ? {
        ...labels,
        preparing: "正在准备导入",
        completed: "导入完成",
        failed: "导入失败",
      }[task!.state]
    : task
      ? labels[task.state]
      : "";
  return (
    <div className="downloads-page">
      {feedback}
      {!game && !task && !failure && (
        <div className="empty-state downloads-empty">
          <ArrowDownToLine size={35} strokeWidth={1.2} />
          <h2>暂无下载任务</h2>
          <p>从游戏库选择游戏，即可在这里安装、更新或导入已有游戏。</p>
          <button className="button primary" onClick={onBrowseLibrary}>
            <Library size={17} />
            前往游戏库
          </button>
        </div>
      )}
      {task && !game && (
        <section
          className="download-panel"
          aria-label={taskImporting ? "导入任务" : "安装任务"}
        >
          <div className="download-heading">
            <div>
              <span className="detail-label">{taskLabel}</span>
              <h2>{task.title}</h2>
            </div>
            {task.state === "ready" ? (
              <Clock3 size={22} />
            ) : task.state === "completed" ? (
              <Check size={22} />
            ) : terminal(task) ? (
              <ArrowDownToLine size={22} />
            ) : (
              <LoaderCircle size={22} className="spin" />
            )}
          </div>
          {task.state !== "ready" && (
            <>
              <progress
                aria-label={
                  task.state === "verifying" ? "校验进度" : "安装进度"
                }
                max={100}
                value={Math.min(100, Math.max(0, task.progress))}
              />
              <div className="download-stats">
                <span>{task.progress.toFixed(1)}%</span>
                {task.state === "downloading" && (
                  <>
                    <span>{bytes(task.downloadSpeed)}/s</span>
                    <span>
                      {bytes(task.downloadedBytes)} /{" "}
                      {bytes(task.plan?.downloadSize ?? 0)}
                    </span>
                    {task.etaSeconds != null && (
                      <span>剩余约 {Math.ceil(task.etaSeconds / 60)} 分钟</span>
                    )}
                  </>
                )}
              </div>
              {task.plan && (
                <p className="download-path">{task.plan.installPath}</p>
              )}
            </>
          )}
          {!terminal(task) && task.state !== "ready" && (
            <button
              className="button secondary"
              disabled={busy || task.state === "cancelling"}
              onClick={() => void command("cancel")}
            >
              <X size={15} />
              取消任务
            </button>
          )}
          {task.state === "cancelled" && (
            <p className="field-hint">
              {taskImporting
                ? "导入已取消，原有游戏文件与安装记录已保留。"
                : "已保留完成的文件。再次选择同一目录安装，即可续传。"}
            </p>
          )}
          {task.state === "failed" && (
            <p className="field-hint">
              {taskImporting
                ? "从游戏库重新打开导入，检查目录后可重试。"
                : "从游戏库重新打开安装，选择同一目录可重试或续传。"}
            </p>
          )}
          {task.prerequisite && (
            <p className="field-hint">
              此游戏还需要运行库：{task.prerequisite}
              。请在游戏目录中完成安装后启动。
            </p>
          )}
          {terminal(task) && (
            <button className="button secondary" onClick={onBrowseLibrary}>
              <Library size={16} />
              前往游戏库
            </button>
          )}
        </section>
      )}
      {game && (
        <section
          className="download-panel download-setup"
          aria-labelledby="download-setup-title"
        >
          <div className="download-heading">
            <div>
              <span className="detail-label">
                {importing
                  ? "导入已有游戏"
                  : updating
                    ? "更新游戏"
                    : "新建安装"}
              </span>
              <h2 id="download-setup-title">{`${importing ? "导入" : updating ? "更新" : "安装"} ${game.title}`}</h2>
            </div>
            <FolderOpen size={24} />
          </div>
          <form onSubmit={(event) => void prepare(event)}>
            <p className="modal-description">
              {importing
                ? "选择已有游戏的根目录，校验通过后确认导入。支持重装系统后恢复游戏或重新关联目录。"
                : "先检查下载大小与可用空间，确认后开始。支持中断后使用同一目录续传。"}
            </p>
            <label className="field-label" htmlFor="install-path">
              {importing ? "已有游戏目录" : "游戏安装目录"}
            </label>
            <div className="path-field">
              <input
                id="install-path"
                value={path}
                disabled={busy || (updating && !importing)}
                onChange={(event) => setPath(event.target.value)}
                required
              />
              <button
                type="button"
                className="button secondary"
                disabled={busy || (updating && !importing)}
                onClick={() => void chooseDirectory()}
              >
                <FolderOpen size={17} />
                选择目录
              </button>
            </div>
            <p className="field-hint">
              {importing
                ? `平台：${platform}。请选择此游戏的专用目录，游戏文件会完整保留。`
                : `游戏文件会直接写入此目录。平台：${platform}。首次安装请选择空目录。`}
            </p>
            <div className="download-actions">
              <button
                className="button primary"
                disabled={busy || !path.trim() || (!!task && !terminal(task))}
              >
                {busy ? (
                  <LoaderCircle className="spin" size={17} />
                ) : (
                  <ArrowDownToLine size={17} />
                )}
                {importing ? "检查已有文件" : "检查安装"}
              </button>
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={onClose}
              >
                取消设置
              </button>
            </div>
          </form>
        </section>
      )}
      {!game && task?.state === "ready" && task.plan && (
        <section
          className="download-panel download-review"
          aria-labelledby="download-review-title"
        >
          <h2 id="download-review-title">{`确认${taskImporting ? "导入" : "安装"} ${task.title}`}</h2>
          <dl className="install-summary">
            <dt>安装目录</dt>
            <dd>{task.plan.installPath}</dd>
            {taskImporting &&
              task.plan.previousPath &&
              task.plan.previousPath !== task.plan.installPath && (
                <>
                  <dt>原安装目录</dt>
                  <dd>{task.plan.previousPath}</dd>
                </>
              )}
            {task.plan.importSummary && (
              <>
                <dt>校验通过</dt>
                <dd>
                  {task.plan.importSummary.matchedFiles} /{" "}
                  {task.plan.importSummary.totalFiles} 个文件
                </dd>
                <dt>缺失 / 不匹配</dt>
                <dd>
                  {task.plan.importSummary.missingFiles} /{" "}
                  {task.plan.importSummary.changedFiles} 个文件
                </dd>
              </>
            )}
            <dt>下载大小</dt>
            <dd>{bytes(task.plan.downloadSize)}</dd>
            <dt>安装大小</dt>
            <dd>{bytes(task.plan.installSize)}</dd>
            <dt>需要额外空间</dt>
            <dd>{bytes(task.plan.requiredSpace)}</dd>
            {task.plan.freeSpace != null && (
              <>
                <dt>可用空间</dt>
                <dd>{bytes(task.plan.freeSpace)}</dd>
              </>
            )}
            <dt>版本 / 平台</dt>
            <dd>
              {task.plan.version} / {task.plan.platform}
            </dd>
          </dl>
          {!!task.plan.importSummary?.problemFiles.length && (
            <div className="install-warnings">
              <h3>未通过校验的文件（最多显示 5 项）</h3>
              <ul>
                {task.plan.importSummary.problemFiles.map((file) => (
                  <li key={file}>{file}</li>
                ))}
              </ul>
            </div>
          )}
          {task.plan.warnings.length > 0 && (
            <div className="install-warnings">
              <h3>安装提醒</h3>
              <ul>
                {task.plan.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
          {task.plan.failures.length > 0 && (
            <div role="alert" className="feedback error">
              <ul>
                {task.plan.failures.map((failure) => (
                  <li key={failure}>{failure}</li>
                ))}
              </ul>
            </div>
          )}
          <p className="field-hint">
            {taskImporting
              ? "导入仅登记已校验的游戏，不下载、覆盖或删除文件。缺失或不匹配的文件需先修复，再重新检查。"
              : "安装游戏本体及全部语言文件。关闭应用会中断任务，下次可续传。"}
          </p>
          <div className="download-actions">
            <button
              className="button primary"
              disabled={
                busy || task.state !== "ready" || task.plan.failures.length > 0
              }
              onClick={() => void command("start")}
            >
              {taskImporting
                ? "确认导入"
                : `确认并开始${task.plan.updating ? "更新" : "安装"}`}
            </button>
            <button
              className="button secondary"
              disabled={busy}
              onClick={() => void command("cancel")}
            >
              取消此计划
            </button>
          </div>
        </section>
      )}
      {(game || task) && (
        <p className="downloads-note">
          一次处理一个任务。切换页面会继续当前任务；关闭应用会中断任务，下载可使用同一目录续传。
        </p>
      )}
    </div>
  );
}
