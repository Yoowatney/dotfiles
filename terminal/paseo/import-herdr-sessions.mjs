// Brings the Claude sessions of the open herdr panes into the Paseo workspace of the same name. Only reads herdr.
// Run it with Paseo's own runtime, because the daemon client lives inside app.asar:
//   ELECTRON_RUN_AS_NODE=1 "<Paseo Helper>" import-herdr-sessions.mjs [pane id ...]
import { execFileSync } from 'node:child_process';

const RES = '/Applications/Paseo.app/Contents/Resources';
const { connectToDaemon } = await import(`${RES}/app.asar/node_modules/@getpaseo/cli/dist/utils/client.js`);

const json = (cmd, ...args) => JSON.parse(execFileSync(cmd, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 1 << 26 }));
const paseo = (...args) => execFileSync('/opt/homebrew/bin/paseo', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });

const only = process.argv.slice(2);
const labels = Object.fromEntries(json('herdr', 'workspace', 'list').result.workspaces.map((w) => [w.workspace_id, w.label]));
const panes = json('herdr', 'pane', 'list').result.panes
  .filter((p) => p.agent === 'claude' && p.agent_session?.value && (!only.length || only.includes(p.pane_id)));
const targets = json('/opt/homebrew/bin/paseo', 'workspace', 'ls', '--json');

const client = await connectToDaemon({ target: { kind: 'instance', home: `${process.env.HOME}/.paseo` } });
let failed = 0;
for (const pane of panes) {
  const name = (pane.label || pane.terminal_title_stripped || pane.pane_id).replace(/^[\p{Extended_Pictographic}️\s]+/u, '');
  const target = targets.find((w) => w.name === labels[pane.workspace_id] && w.cwd === pane.cwd) ?? targets.find((w) => w.cwd === pane.cwd);
  if (!target) { console.log(`SKIP ${pane.pane_id} ${name}: no workspace for this folder`); continue; }
  try {
    const agent = await client.importAgent({ provider: 'claude', sessionId: pane.agent_session.value, cwd: pane.cwd, workspaceId: target.workspaceId });
    paseo('agent', 'update', agent.id, '--name', name, '--thinking', 'xhigh'); // Paseo's default shows Medium; herdr's Claude runs xhigh
    paseo('agent', 'mode', agent.id, 'bypassPermissions');
    console.log(`OK   ${pane.pane_id} -> ${target.name} ${agent.id.slice(0, 7)} ${name}`);
  } catch (error) {
    if (/already imported/.test(error.message)) { console.log(`HAVE ${pane.pane_id} ${name}`); continue; }
    failed++;
    console.log(`FAIL ${pane.pane_id} ${name}: ${error.message.slice(0, 160)}`);
  }
}
await client.close();
process.exit(failed ? 1 : 0);
