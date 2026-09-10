# dotfiles

macOS development environment dotfiles.

## Includes

- **Neovim** - LazyVim based config
- **Tmux** - gpakosz/.tmux based config
- **Zsh** - Oh-My-Zsh + Powerlevel10k
- **Lazygit** - Custom keybindings
- **mise** - Runtime version manager
- **iTerm2** - Settings backup
- **Brewfile** - Homebrew packages

## Installation

```bash
git clone https://github.com/USERNAME/dotfiles.git ~/.dotfiles
cd ~/.dotfiles
./init.sh
```

### Server MacBook (reached with `herdr --remote`)

```bash
./init.sh --server                    # same, but leaves Remote Login (sshd) on
sudo tailscale up --operator=$USER    # browser auth; init.sh already started tailscaled
```

From the client, once: `./scripts/setup-ssh.sh <tailscale-hostname> <user>`, then `herdr --remote <user>@<tailscale-hostname>`.

Gotchas:
- Run `herdr --remote` from a plain terminal window, not inside a herdr pane (nested herdr is off by default).
- Keep herdr at the same version on both machines (`brew upgrade herdr`). An older local client cannot talk to a newer remote server; it stops the server and installs its own copy into `~/.local/bin` on the remote, bypassing the Brewfile one.

## What init.sh does

1. Install Homebrew (if not installed)
2. Install packages from Brewfile
3. Install Oh-My-Zsh + plugins (p10k, autosuggestions, syntax-highlighting)
4. Install gpakosz/.tmux + TPM
5. Create symlinks (existing files backed up to `.bak`)
6. Set zsh as default shell

## Symlinks

| Source | Target |
|--------|--------|
| `nvim/` | `~/.config/nvim` |
| `zsh/zshrc` | `~/.zshrc` |
| `zsh/p10k.zsh` | `~/.p10k.zsh` |
| `tmux/tmux.conf.local` | `~/.tmux.conf.local` |
| `lazygit/` | `~/.config/lazygit` |
| `mise/config.toml` | `~/.config/mise/config.toml` |

## mise

Runtime version manager (asdf 대체)

**Tools:**
- node 22, python 3.11, terraform latest

**Global npm packages:**
- claude-code, clasp, gemini-cli, codex, ccusage, firebase-tools, nodemon

## Zsh Aliases

| Alias | Command |
|-------|---------|
| `v`, `vi`, `vim` | nvim |
| `g` | lazygit |
| `gs` | git status |
| `gl` | git log --graph --oneline --all --decorate |
| `ls` | lsd |
| `lt` | lsd --tree |
| `diff` | delta |
| `rm` | trash |

## Lazygit Custom Commands

| Key | Context | Action |
|-----|---------|--------|
| `C` | files | git commit (with gitmoji) |
| `Ctrl+p` | global | Push to specific remote/branch |
| `P` | global | Force push with lease (safe) |
| `O` | localBranches | Create PR via gh |
| `S` | files | Stash with message |
| `Ctrl+D` | localBranches | Delete merged branches |

## Post Install

- Restart terminal or `source ~/.zshrc`
- In tmux, press `prefix + I` to install plugins
