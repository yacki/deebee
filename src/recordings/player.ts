import { nextTick, onBeforeUnmount, ref, shallowRef } from 'vue';
import Guacamole from 'guacamole-common-js';
import { Terminal } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { API_BASE, authToken } from '../api';

export type Recording = { id:string; protocol:'ssh'|'rdp'; actor:string; target:string; host:string; username:string; started_at:number; duration:number; status:string; raw_bytes:number; stored_bytes:number; error:string; sha256:string };
type CastEvent = [number, string, string];
function allowEmptySnapshots(display: Guacamole.Display) {
  // Guacamole 1.5 snapshots every protocol buffer, including valid 0×0
  // buffers. Its Layer.toCanvas draws those empty canvases and throws in
  // Chromium. Preserve their dimensions without drawing an empty source.
  function protect<T extends Guacamole.Layer>(layer: T): T {
    const snapshot = layer.toCanvas.bind(layer);
    layer.toCanvas = () => {
      const source = layer.getCanvas();
      if (source.width && source.height) return snapshot();
      const canvas = document.createElement('canvas');
      canvas.width = layer.width; canvas.height = layer.height;
      return canvas;
    };
    return layer;
  }
  protect(display.getDefaultLayer());
  const createLayer = display.createLayer.bind(display), createBuffer = display.createBuffer.bind(display);
  display.createLayer = () => protect(createLayer());
  display.createBuffer = () => protect(createBuffer());
}
export function elapsed(ms: number) {
  const seconds = Math.floor(Math.max(0, ms) / 1000);
  return `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
}

export function useRecordingPlayer() {
  const selected = shallowRef<Recording>();
  const viewport = ref<HTMLElement>();
  const loading = ref(false), error = ref(''), playing = ref(false), seeking = ref(false);
  const position = ref(0), duration = ref(0), speed = ref(1);
  let recording: Guacamole.SessionRecording | undefined, terminal: Terminal | undefined;
  let observer: ResizeObserver | undefined, controller: AbortController | undefined;
  let frameTimes: number[] = [], renderedFrame = -1;
  let events: CastEvent[] = [], eventIndex = 0, size = { cols: 120, rows: 32 };
  let generation = 0, operation = 0, timer: ReturnType<typeof setInterval> | undefined;
  let renderQueue = Promise.resolve(), clock = 0, origin = 0, rendering = false;
  let resumeAfterSeek = false;

  function pause() {
    playing.value = false;
    if (timer) clearInterval(timer);
    timer = undefined;
  }
  function scale() {
    if (!recording || !viewport.value) return;
    const display = recording.getDisplay();
    if (display.getWidth()) display.scale(Math.min(1, viewport.value.clientWidth / display.getWidth(), Math.max(240, innerHeight * 0.55) / Math.max(1, display.getHeight())));
  }
  function close() {
    generation++; operation++;
    pause(); controller?.abort(); controller = undefined;
    recording?.cancel(); recording?.abort(); recording = undefined;
    terminal?.dispose(); terminal = undefined;
    observer?.disconnect(); observer = undefined;
    viewport.value?.replaceChildren();
    frameTimes = []; renderedFrame = -1; events = []; eventIndex = 0; renderQueue = Promise.resolve(); rendering = false;
    selected.value = undefined; position.value = 0; duration.value = 0;
    loading.value = false; seeking.value = false; resumeAfterSeek = false;
  }
  function write(target: Terminal, data: string) {
    return new Promise<void>(resolve => target.write(data, resolve));
  }
  function renderSsh(target: number, reset: boolean, active: number, request: number) {
    // Serialize xterm writes: a reset must wait for the previous write to finish.
    const task = renderQueue.then(async () => {
      const term = terminal;
      const current = () => active === generation && request === operation && term === terminal;
      if (!term || !current()) return;
      if (reset) { term.reset(); term.resize(size.cols, size.rows); eventIndex = 0; }
      let batch = '';
      while (eventIndex < events.length && events[eventIndex]![0] * 1000 <= target) {
        if (!current()) return;
        const event = events[eventIndex++]!;
        if (event[1] === 'o') batch += event[2];
        if (event[1] === 'r' || batch.length >= 65536) {
          if (batch) { await write(term, batch); batch = ''; }
          if (!current()) return;
          if (event[1] === 'r') {
            const [cols, rows] = event[2].split('x').map(Number);
            if (cols && rows) term.resize(cols, rows);
          }
        }
      }
      if (batch && current()) await write(term, batch);
    });
    renderQueue = task.catch(() => {});
    return task;
  }
  function renderFrame(target: number, active: number, request: number) {
    const player = recording;
    if (!player) return renderSsh(target, false, active, request);
    const task=renderQueue.then(async()=>{
      if(active!==generation||request!==operation)return;
      let low = 0, high = frameTimes.length - 1;
      while (low < high) {
        const middle = Math.ceil((low + high) / 2);
        if (frameTimes[middle]! <= target) low = middle; else high = middle - 1;
      }
      if (low === renderedFrame) return;
      // Guacamole finishes parsing a seek before its image draws necessarily
      // finish. Wait for those draws (and keyframe snapshots) before allowing
      // another seek to restore state and cancel pending display operations.
      await new Promise<void>(resolve => player.seek(frameTimes[low] ?? 0, () => player.getDisplay().flush(resolve)));
      // Even a superseded request changed the engine; the next queued seek
      // must compare against the frame that was actually rendered.
      if (active === generation) { renderedFrame = low; scale(); }
    });
    renderQueue=task.catch(()=>{});
    return task;
  }
  function play() {
    if (playing.value || loading.value || seeking.value || error.value) return;
    if (position.value >= duration.value) { void seek(0, true); return; }
    if (!terminal && !recording) return;
    playing.value = true; origin = position.value; clock = performance.now();
    const active = generation, request = operation;
    timer = setInterval(() => {
      if (rendering || !playing.value || active !== generation || request !== operation) return;
      const target = Math.min(duration.value, origin + (performance.now() - clock) * speed.value);
      rendering = true;
      void renderFrame(target, active, request).then(() => {
        if (active !== generation || request !== operation) return;
        position.value = target;
        if (target >= duration.value) pause();
      }).catch(reason => { if (active === generation && request === operation) fail(reason); }).finally(() => { if (active === generation && request === operation) rendering = false; });
    }, 40);
  }
  function fail(reason: unknown) { error.value = reason instanceof Error ? reason.message : '录像播放失败'; pause(); seeking.value = false; }
  async function seek(value: number, resume?: boolean) {
    if (loading.value || error.value || (!recording && !terminal)) return;
    const active = generation, request = ++operation;
    const target = Math.max(0, Math.min(duration.value, value));
    resumeAfterSeek = resume ?? (seeking.value ? resumeAfterSeek : playing.value);
    pause(); seeking.value = true; rendering = false;
    try {
      if (recording) await renderFrame(target, active, request);
      else await renderSsh(target, true, active, request);
      if (active !== generation || request !== operation) return;
      position.value = target;
      scale();
      seeking.value = false;
      if (resumeAfterSeek && target < duration.value) play();
    } catch (reason) { if (active === generation && request === operation) fail(reason); }
  }
  function changeSpeed(value: number) {
    const resume = playing.value;
    pause(); speed.value = value;
    if (resume) play();
  }
  async function open(row: Recording) {
    close(); selected.value = row; loading.value = true; error.value = ''; speed.value = 1;
    const active = generation;
    controller = new AbortController();
    try {
      // The caller renders the viewport in response to selected before loading.
      await nextTick();
      if (!viewport.value || active !== generation) return;
      if (row.protocol === 'rdp') {
        const player = new Guacamole.SessionRecording(new Guacamole.StaticHTTPTunnel(`${API_BASE}/admin/v1/session-recordings/${row.id}/content`, false, { Authorization: `Bearer ${authToken()}` }));
        allowEmptySnapshots(player.getDisplay());
        recording = player;
        viewport.value.appendChild(player.getDisplay().getElement());
        player.getDisplay().onresize = scale;
        observer = new ResizeObserver(scale); observer.observe(viewport.value);
        player.onprogress = ms => { if (active === generation) { duration.value = ms; frameTimes.push(ms); } };
        player.onseek = () => { if (active === generation) scale(); };
        player.onerror = message => { if (active === generation) { loading.value = false; fail(new Error(message)); } };
        player.onload = () => {
          if (active !== generation) return;
          duration.value = player.getDuration(); loading.value = false;
          if (duration.value > 0) play();
          else fail(new Error('录像没有可回放的画面'));
        };
        player.connect();
      } else {
        const response = await fetch(`${API_BASE}/admin/v1/session-recordings/${row.id}/content`, { headers: { Authorization: `Bearer ${authToken()}` }, signal: controller.signal });
        if (!response.ok) throw new Error((await response.json().catch(() => null))?.error?.message || '无法读取录像');
        const lines = (await response.text()).trim().split('\n');
        if (active !== generation) return;
        const header = JSON.parse(lines.shift() || '{}');
        if (header.version !== 2) throw new Error('不支持的终端录像格式');
        size = { cols: header.width || 120, rows: header.height || 32 };
        events = lines.filter(Boolean).map(line => JSON.parse(line) as CastEvent);
        if (events.some(event => !Array.isArray(event) || !Number.isFinite(event[0]) || event[0] < 0 || typeof event[2] !== 'string')) throw new Error('终端录像数据损坏');
        events.sort((a, b) => a[0] - b[0]);
        duration.value = Math.max(row.duration * 1000, (events.at(-1)?.[0] || 0) * 1000, 1);
        terminal = new Terminal({ cols: size.cols, rows: size.rows, fontSize: 12, disableStdin: true, cursorBlink: false, scrollback: 3000, theme: { background: '#0b1220', foreground: '#d9e2f2' } });
        terminal.open(viewport.value); loading.value = false; play();
      }
    } catch (reason) { if (active === generation) { loading.value = false; fail(reason); } }
  }
  onBeforeUnmount(close);
  return { selected, viewport, loading, error, playing, seeking, position, duration, speed, open, close, play, pause, seek, changeSpeed };
}
