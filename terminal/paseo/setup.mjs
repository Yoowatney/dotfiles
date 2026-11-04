#!/usr/bin/env node
// Applies the herdr environment to Paseo. Re-runnable on any Mac with Paseo installed:
//   node ~/.dotfiles/terminal/paseo/setup.mjs
// It quits the app and stops the daemon first, so it closes Paseo's terminals and interrupts its agents. Run it outside Paseo.
//
// Trap: never start Paseo or its daemon from an agent's shell with its environment (`open -a Paseo`, `paseo daemon start`).
// `open` hands the caller's environment to the app and the app to the daemon, which outlives the app. Every Paseo agent then
// inherits HERDR_PANE_ID, so its pane tools act on that pane, and CLAUDE_CODE_CHILD_SESSION, which turns transcripts off.
// `ps eww` does not show this: the daemon rewrites its process title over that memory.
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const HOME = homedir();
const DEBUG_PORT = 9333;

// Binding ids come from Paseo's packages/app/src/keyboard/keyboard-shortcuts.ts; the left side of each comment is the
// herdr action in terminal/herdr/config.toml. Paseo has no action for next_agent, focus_agent, last_pane or resize_mode.
const SHORTCUTS = {
  'workspace-pane-split-right-cmd-backslash': 'Ctrl+A V', // split_vertical
  'workspace-pane-split-down-cmd-shift-backslash': 'Ctrl+A Shift+V', // split_horizontal
  'workspace-pane-focus-left-cmd-shift-left': 'Ctrl+H', // pane-focus-wrap.sh left; Paseo does not wrap at the edge
  'workspace-pane-focus-down-cmd-shift-down': 'Ctrl+J', // focus_pane_down
  'workspace-pane-focus-up-cmd-shift-up': 'Ctrl+K', // focus_pane_up
  'workspace-pane-focus-right-cmd-shift-right': 'Ctrl+L', // pane-focus-wrap.sh right
  'workspace-pane-close-cmd-shift-w': 'Ctrl+A X', // close_pane
  'workspace-tab-new-cmd-t-mac': 'Ctrl+A C', // new_tab
  'workspace-tab-close-current-cmd-w-mac': 'Ctrl+A Shift+X', // close_tab
  'workspace-tab-navigate-relative-alt-shift-left': 'Ctrl+A P', // previous_tab
  'workspace-tab-navigate-relative-alt-shift-right': 'Ctrl+A N', // next_tab
  'workspace-tab-navigate-index-cmd-alt-digit-mac-desktop': 'Ctrl+A Digit', // switch_tab
  'workspace-navigate-relative-cmd-left-mac': 'Ctrl+A ArrowLeft', // previous_workspace
  'workspace-navigate-relative-cmd-right-mac': 'Ctrl+A ArrowRight', // next_workspace
  'workspace-new-cmd-n-mac': 'Ctrl+A Shift+N', // new_workspace
  'command-center-toggle-cmd-k-mac': 'Ctrl+A S', // goto
  'settings-toggle-cmd-comma-mac': 'Ctrl+A G', // settings
  'sidebar-toggle-left-mac-cmd-b': 'Ctrl+A B', // toggle_sidebar
  'view-toggle-focus-cmd-shift-f-mac': 'Ctrl+A Z', // zoom
};

// A chat agent runs in bypass as the `claude` alias in .zshrc does, on the model Claude settings name. Codex keeps its own
// default: Paseo's auto-review matches approvals_reviewer in ~/.codex/config.toml.
const CLAUDE_DEFAULTS = { mode: 'bypassPermissions', model: 'opus' };

const DAEMON_CONFIG = {
  // create_agent reports back to the agent that opened a worker; `paseo run` from an agent does not.
  'daemon.mcp.injectIntoAgents': true,
  'daemon.appendSystemPrompt':
    'This session runs in Paseo, not herdr. Pane tools such as pane-label do nothing here, so ignore the pane name and ' +
    'mark rules. To hand work to another agent use the create_agent tool instead of pane-spawn, because it notifies you ' +
    'when the worker finishes, and use send_agent_prompt instead of pane-tell.',
  // The three panes of an L task, from the design, build and review split.
  'daemon.agentProfiles': [
    { id: 'design', name: '설계', provider: 'claude', model: 'claude-opus-5-5', modeId: 'bypassPermissions', thinkingOptionId: 'xhigh',
      notes: 'Design of an L task. Ends with a writing-plans plan file.' },
    { id: 'build', name: '구현', provider: 'claude', model: 'claude-opus-5-5', modeId: 'bypassPermissions', thinkingOptionId: 'high',
      notes: 'Builds from a plan file; give it only the file path.' },
    { id: 'review', name: '리뷰', provider: 'codex', model: 'gpt-6.1-sol', thinkingOptionId: 'xhigh',
      notes: 'Independent review of the finished diff.' },
  ],
  // The full terminal UI. Its Claude runs every hook, while a chat agent counts as an SDK session (see the report guards).
  'daemon.terminalProfiles': [
    { id: 'claude', name: 'Claude Code', command: 'claude', args: ['--dangerously-skip-permissions', '{{{prompt}}}'], icon: 'claude' },
    { id: 'codex', name: 'Codex', command: 'codex', args: ['{{{prompt}}}'], icon: 'codex' },
  ],
  // A herdr session stays up however long it idles; Paseo would otherwise stop an idle agent's process after an hour.
  'agents.idleRuntimeCleanup': false,
};

// The herdr workspaces. A Paseo workspace has one directory, so herdr's Home splits in two.
const WORKSPACES = [
  ['Work', 'work/munice'],
  ['Main', 'work/munice'],
  ['Research', 'work/munice'],
  ['Home', 'home'],
  ['gomgom', 'work/gomgom'],
];

const paseo = (...args) => execFileSync('paseo', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function until(what, test, seconds = 60) {
  for (let i = 0; i < seconds * 2; i++) {
    try {
      const value = await test();
      if (value) return value;
    } catch {} // the target is not up yet
    await sleep(500);
  }
  throw new Error(`timed out waiting for ${what}`);
}

const appRunning = () => spawnSync('pgrep', ['-x', 'Paseo']).status === 0;
const openApp = (...args) =>
  execFileSync('/usr/bin/open', ['-a', 'Paseo', ...args], { env: { HOME, USER: process.env.USER, PATH: '/usr/bin:/bin' } });

async function quitApp() {
  spawnSync('osascript', ['-e', 'quit app "Paseo"']);
  await until('Paseo to quit', () => !appRunning());
}

function writeDesktopSettings() {
  const file = join(HOME, 'Library/Application Support/Paseo/desktop-settings.json');
  const saved = existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : { version: 1 };
  const settings = saved.settings ?? {};
  saved.settings = {
    ...settings,
    notifications: { ...settings.notifications, playSound: false }, // [ui.sound] enabled = false
    daemon: { ...settings.daemon, keepRunningAfterQuit: true }, // sessions outlive the window, like the herdr server
  };
  writeFileSync(file, `${JSON.stringify(saved, null, 2)}\n`);
}

// Shortcuts and new-agent defaults live in the app's localStorage, which has no file or CLI, so they go in through the
// DevTools port of one short-lived launch bound to this machine.
async function writeAppStorage() {
  openApp('--args', `--remote-debugging-port=${DEBUG_PORT}`);
  const page = await until('the Paseo window', async () => {
    const targets = await (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`)).json();
    return targets.find((target) => target.type === 'page');
  });
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((open, fail) => { socket.onopen = open; socket.onerror = fail; });
  const expression = `(() => {
    const read = (key) => JSON.parse(localStorage.getItem(key) || '{}');
    const keys = '@paseo:keyboard-shortcut-overrides';
    localStorage.setItem(keys, JSON.stringify({ ...read(keys), ...${JSON.stringify(SHORTCUTS)} }));
    const prefsKey = '@paseo:create-agent-preferences';
    const prefs = read(prefsKey);
    const byProvider = prefs.providerPreferences || {};
    localStorage.setItem(prefsKey, JSON.stringify({ ...prefs, provider: 'claude', providerPreferences: {
      ...byProvider, claude: { ...byProvider.claude, ...${JSON.stringify(CLAUDE_DEFAULTS)} } } }));
  })()`;
  const reply = await new Promise((done) => {
    socket.onmessage = (event) => done(JSON.parse(event.data));
    socket.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression } }));
  });
  socket.close();
  if (reply.result?.exceptionDetails) throw new Error(`app storage: ${reply.result.exceptionDetails.text}`);
}

function createWorkspaces() {
  const existing = JSON.parse(paseo('workspace', 'ls', '--json'));
  for (const [title, dir] of WORKSPACES) {
    const path = join(HOME, dir);
    if (!existsSync(path) || existing.some((w) => w.name === title && w.cwd === path)) continue;
    paseo('workspace', 'create', '--isolation', 'local', '--path', path, '--title', title);
  }
}

await quitApp();
// keepRunningAfterQuit leaves it up on a re-run, and the app adopts a running daemon with whatever environment it has.
spawnSync('paseo', ['daemon', 'stop']);
await until('the Paseo daemon to stop', () => JSON.parse(paseo('daemon', 'status', '--json')).localDaemon === 'stopped');
for (const [path, value] of Object.entries(DAEMON_CONFIG)) paseo('daemon', 'config', 'set', path, JSON.stringify(value));
writeDesktopSettings();
await writeAppStorage();
await quitApp(); // the shortcut and preference stores read localStorage once, at launch
openApp();
await until('the Paseo daemon', () => JSON.parse(paseo('daemon', 'status', '--json')).localDaemon === 'running');
createWorkspaces();
console.log(paseo('workspace', 'ls').trim());
