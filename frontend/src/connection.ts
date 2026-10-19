// Browser transport only. Trading/data freshness decisions remain on the backend.
export type ConnectionStatus = {
  state: "connecting" | "stream" | "polling" | "offline" | "display-error";
  reconnects: number;
  ageSeconds: number | null;
  detail?: string;
};

export function startConnection(render: (value: any) => void, status: (value: ConnectionStatus) => void) {
  const FRESH_FOR = 5000, HANDSHAKE_TIMEOUT = 30000, STREAM_TIMEOUT = 15000;
  const POLL_INTERVAL = 2000, POLL_TIMEOUT = 4000;
  let ws: WebSocket | null = null, generation = 0, retryAt = 0, failures = 0, reconnects = 0;
  let socketStarted = 0, lastStream: number | null = null, lastRendered: number | null = null;
  let lastPoll = -Infinity, streamRevision = 0, displayError = false, stopped = false;
  let renderedVersion: {runtime_id: string; sequence: number} | null = null;
  let current: ConnectionStatus["state"] = "connecting", currentDetail: string | undefined;
  type Poll = {controller: AbortController; deadline: number; timer: ReturnType<typeof setTimeout>};
  let request: Poll | null = null;
  // Some browsers pause performance.now() during device sleep. Advance by either
  // clock so a suspended tab cannot keep a dead connection alive on resume.
  let wall = Date.now(), monotonic = performance.now(), elapsed = 0;
  function now() {
    const nextWall = Date.now(), nextMonotonic = performance.now();
    const monotonicDelta = Math.max(0, nextMonotonic - monotonic);
    elapsed += monotonicDelta;
    // Ignore normal wall-clock rounding; only add a meaningful sleep/clock gap.
    if (nextWall - wall - monotonicDelta > 1000) elapsed += nextWall - wall - monotonicDelta;
    wall = nextWall; monotonic = nextMonotonic;
    return elapsed;
  }
  const started = now();
  function report(state: ConnectionStatus["state"], detail?: string) {
    currentDetail = detail ?? (state === current ? currentDetail : undefined);
    current = state;
    status({state, reconnects, ageSeconds: lastRendered === null ? null : (now() - lastRendered) / 1000, detail: currentDetail});
  }
  function accept(value: unknown, source: "stream" | "polling") {
    const candidate = (value as {stream_version?: {runtime_id?: unknown; sequence?: unknown}} | null)?.stream_version;
    const version = candidate && typeof candidate.runtime_id === "string" && candidate.runtime_id.length > 0
      && typeof candidate.sequence === "number" && Number.isSafeInteger(candidate.sequence) && candidate.sequence >= 0
      ? {runtime_id: candidate.runtime_id, sequence: candidate.sequence} : null;
    if (version && renderedVersion && version.runtime_id === renderedVersion.runtime_id
      && version.sequence < renderedVersion.sequence) return;
    try {
      render(value);
      renderedVersion = version;
      lastRendered = now();
      displayError = false;
      report(source);
    } catch (error) {
      if (!displayError) console.error("Dashboard could not display backend state:", error);
      displayError = true;
      report("display-error", error instanceof Error ? error.message : "Invalid backend update");
    }
  }
  function closeSocket(socket: WebSocket) {
    socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null;
    // close() is best-effort: browsers need not deliver onclose on a broken path.
    try {socket.close();} catch { /* The replacement owns recovery. */ }
  }
  function scheduleRetry() {
    const base = Math.min(30000, 1000 * 2 ** Math.min(failures++, 5));
    retryAt = now() + Math.min(30000, base * (0.8 + Math.random() * 0.4));
    reconnects++;
  }
  function retire(socket: WebSocket) {
    if (socket !== ws) return;
    ws = null; generation++;
    closeSocket(socket);
    scheduleRetry();
  }
  function cancelPoll() {
    const pending = request;
    request = null;
    if (pending) {clearTimeout(pending.timer); pending.controller.abort();}
  }
  function connect() {
    if (stopped) return;
    socketStarted = now(); lastStream = null;
    const owner = ++generation;
    let socket: WebSocket;
    try {socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/stream`);}
    catch {scheduleRetry(); return;}
    ws = socket;
    const ownsSocket = () => !stopped && socket === ws && owner === generation;
    socket.onmessage = event => {
      if (!ownsSocket()) return;
      lastStream = now(); failures = 0; streamRevision++;
      cancelPoll();
      try {accept(JSON.parse(event.data), "stream");}
      catch (error) {
        if (!displayError) console.error("Dashboard could not display backend state:", error);
        displayError = true; report("display-error", "The backend sent an unreadable update.");
      }
    };
    socket.onclose = socket.onerror = () => {
      if (!ownsSocket()) return;
      retire(socket);
      // A closed socket does not pause the bot; HTTP can still update the UI.
      tick();
    };
  }
  async function poll() {
    const controller = new AbortController(), before = streamRevision;
    lastPoll = now();
    const pending: Poll = {controller, deadline: lastPoll + POLL_TIMEOUT, timer: 0};
    request = pending;
    pending.timer = setTimeout(() => {
      if (request !== pending || stopped) return;
      cancelPoll();
      tick();
    }, POLL_TIMEOUT);
    try {
      const response = await fetch("/api/state", {cache: "no-store", signal: controller.signal});
      if (!response.ok) throw Error(`Backend returned HTTP ${response.status}`);
      const value = await response.json();
      // Ownership also rejects responses from timed-out/aborted requests, even
      // when a browser or proxy delivers them after a newer request or stream.
      if (!stopped && request === pending && before === streamRevision && now() < pending.deadline) accept(value, "polling");
    } catch {
      if (!stopped && request === pending && (lastRendered === null || now() - lastRendered > FRESH_FOR) && !displayError) report("offline");
    } finally {
      clearTimeout(pending.timer);
      if (request === pending) request = null;
    }
  }
  function tick() {
    if (stopped) return;
    const time = now();
    if (request && time >= request.deadline) cancelPoll();
    // Retire ownership before closing. Recovery never waits on a readyState
    // transition or close event, including sockets stuck in CONNECTING/CLOSING.
    if (ws && (ws.readyState === WebSocket.CLOSING || ws.readyState === WebSocket.CLOSED
      || (lastStream === null ? time - socketStarted >= HANDSHAKE_TIMEOUT : time - lastStream >= STREAM_TIMEOUT))) retire(ws);
    const streamFresh = lastStream !== null && time - lastStream <= FRESH_FOR && ws?.readyState === WebSocket.OPEN;
    if (!streamFresh && time - started >= FRESH_FOR && !request && time - lastPoll >= POLL_INTERVAL) void poll();
    if (!displayError && (lastRendered === null ? time - started >= FRESH_FOR : time - lastRendered > FRESH_FOR)) report("offline");
    else report(current);
    if (!ws && time >= retryAt) connect();
  }
  function recover(networkChanged = false) {
    if (stopped) return;
    const time = now();
    if (networkChanged || lastStream === null || time - lastStream > FRESH_FOR) {
      cancelPoll();
      if (ws) retire(ws);
      // A user returning to the app or a restored network gets an immediate
      // attempt, without waiting for a pre-sleep timeout/backoff to expire.
      retryAt = time; lastPoll = -Infinity;
    }
    tick();
  }
  const resume = () => {if (!document.hidden) recover();};
  const online = () => recover(true);
  const restored = (event: PageTransitionEvent) => {if (event.persisted) resume();};
  report("connecting"); connect();
  const timer = setInterval(tick, 1000);
  document.addEventListener("visibilitychange", resume);
  window.addEventListener("online", online);
  window.addEventListener("pageshow", restored);
  return () => {
    stopped = true; clearInterval(timer); cancelPoll(); generation++;
    const socket = ws; ws = null;
    if (socket) closeSocket(socket);
    document.removeEventListener("visibilitychange", resume);
    window.removeEventListener("online", online);
    window.removeEventListener("pageshow", restored);
  };
}
