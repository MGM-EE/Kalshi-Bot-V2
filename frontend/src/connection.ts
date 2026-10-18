// Browser transport only. Trading/data freshness decisions remain on the backend.
export type ConnectionStatus = {
  state: "connecting" | "stream" | "polling" | "offline" | "display-error";
  reconnects: number;
  ageSeconds: number | null;
  detail?: string;
};

export function startConnection(render: (value: any) => void, status: (value: ConnectionStatus) => void) {
  let ws: WebSocket | null = null, retryAt = 0, failures = 0, reconnects = 0;
  let socketStarted = performance.now(), lastStream: number | null = null;
  let lastRendered: number | null = null, lastPoll = -Infinity, request: AbortController | null = null;
  let revision = 0, displayError = false, stopped = false;
  let current: ConnectionStatus["state"] = "connecting";
  let currentDetail: string | undefined;
  const started = performance.now();
  function report(state: ConnectionStatus["state"], detail?: string) {
    currentDetail = detail ?? (state === current ? currentDetail : undefined);
    current = state;
    status({state, reconnects, ageSeconds: lastRendered === null ? null : (performance.now() - lastRendered) / 1000, detail: currentDetail});
  }
  function accept(value: unknown, source: "stream" | "polling") {
    try {
      render(value);
      revision++;
      lastRendered = performance.now();
      displayError = false;
      report(source);
    } catch (error) {
      if (!displayError) console.error("Dashboard could not display backend state:", error);
      displayError = true;
      report("display-error", error instanceof Error ? error.message : "Invalid backend update");
    }
  }
  function connect() {
    if (stopped) return;
    socketStarted = performance.now(); lastStream = null;
    const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/stream`);
    ws = socket;
    socket.onmessage = event => {
      if (stopped || socket !== ws) return;
      lastStream = performance.now(); failures = 0;
      try {accept(JSON.parse(event.data), "stream");}
      catch (error) {
        if (!displayError) console.error("Dashboard could not display backend state:", error);
        displayError = true; report("display-error", "The backend sent an unreadable update.");
      }
    };
    socket.onclose = () => {
      if (stopped || socket !== ws) return;
      ws = null; reconnects++;
      retryAt = performance.now() + Math.min(30000, 1000 * 2 ** Math.min(failures++, 5));
      // The HTTP backup may still be fresh. A closed socket does not pause the bot.
      tick();
    };
    socket.onerror = () => {if (socket.readyState !== WebSocket.CLOSED) socket.close();};
  }
  async function poll() {
    const controller = new AbortController(); request = controller;
    const before = revision;
    lastPoll = performance.now();
    const timeout = setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch("/api/state", {cache: "no-store", signal: controller.signal});
      if (!response.ok) throw Error(`Backend returned HTTP ${response.status}`);
      const value = await response.json();
      // A response started before a newer streamed frame must not rewind the UI.
      if (!stopped && before === revision) accept(value, "polling");
    } catch {
      if (!stopped && (lastRendered === null || performance.now() - lastRendered > 5000) && !displayError) report("offline");
    } finally {
      clearTimeout(timeout);
      if (request === controller) request = null;
    }
  }
  function tick() {
    if (stopped) return;
    const now = performance.now();
    const streamFresh = lastStream !== null && now - lastStream <= 5000 && ws?.readyState === WebSocket.OPEN;
    if (!streamFresh && now - started >= 5000 && !request && now - lastPoll >= 2000) void poll();
    if (!displayError && (lastRendered === null ? now - started >= 5000 : now - lastRendered > 5000)) report("offline");
    else report(current);
    // Keep the stale warning immediate; give an existing socket time to recover.
    if (ws && (lastStream === null ? now - socketStarted > 30000 : now - lastStream > 15000)
        && ws.readyState !== WebSocket.CLOSING && ws.readyState !== WebSocket.CLOSED) ws.close();
    if (!ws && now >= retryAt) connect();
  }
  report("connecting"); connect();
  const timer = setInterval(tick, 1000);
  const resume = () => {if (!document.hidden) tick();};
  document.addEventListener("visibilitychange", resume);
  window.addEventListener("online", tick);
  return () => {
    stopped = true; clearInterval(timer); request?.abort(); ws?.close();
    document.removeEventListener("visibilitychange", resume);
    window.removeEventListener("online", tick);
  };
}
