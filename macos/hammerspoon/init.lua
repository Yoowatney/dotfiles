-- Hammerspoon 설정
-- Claude Code 알림 + tmux 세션 전환

-- IPC 모듈 로드 (CLI에서 hs 명령어 사용 가능하게)
require("hs.ipc")

-- Claude Code 알림 함수
-- session, window, pane: 알림 클릭 시 이동할 tmux 세션/윈도우/페인
function claudeNotify(title, message, session, window, pane)
    local n = hs.notify.new(function(notification)
        -- 알림 클릭 시 해당 tmux 세션/윈도우/페인으로 이동
        local script = os.getenv("HOME") .. "/.dotfiles/macos/claude-notification/switch-tmux.sh"
        local cmd = script .. " " .. (session or "") .. " " .. (window or "") .. " " .. (pane or "")
        hs.execute(cmd)
    end, {
        title = title or "Claude Code",
        informativeText = message or "작업이 완료되었습니다",
        withdrawAfter = 0,  -- 자동으로 안 사라짐
        hasActionButton = true,
        actionButtonTitle = "이동",
    })
    n:send()
end

-- 입력 필요 알림 (다른 사운드)
function claudeNotifyInput(message, session, window, pane)
    hs.sound.getByFile("/System/Library/Sounds/Blow.aiff"):play()
    claudeNotify("Claude Code - 입력 필요", message or "입력이 필요합니다", session, window, pane)
end

-- 작업 완료 알림
function claudeNotifyDone(message, session, window, pane)
    hs.sound.getByFile("/System/Library/Sounds/Glass.aiff"):play()
    claudeNotify("Claude Code", message or "작업이 완료되었습니다", session, window, pane)
end

-- CLI에서 호출 가능하도록 전역 등록
-- 사용법: hs -c 'claudeNotifyDone("완료 메시지")'
-- 사용법: hs -c 'claudeNotifyInput("입력 필요 메시지")'

-- 설정 리로드 단축키 (Cmd+Ctrl+R)
hs.hotkey.bind({"cmd", "ctrl"}, "r", function()
    hs.reload()
end)

-- Keep Option+R scoped to Ego Lite and open its native Space naming dialog.
egoSpaceRename = {}
local egoBundleID = "com.citrolabs.ego.lite"

function egoSpaceRename.open()
    local app = hs.application.frontmostApplication()
    if not app or app:bundleID() ~= egoBundleID then
        return false, "Ego Lite is not the active app"
    end
    if not app:selectMenuItem({"창", "창 이름 지정…"}) then
        hs.alert.show("Space 이름 변경 메뉴를 열 수 없습니다")
        return false, "Space naming menu is unavailable"
    end
    return true
end

egoSpaceRename.hotkey = hs.hotkey.new({"alt"}, "r", egoSpaceRename.open)
egoSpaceRename.watcher = hs.application.watcher.new(function(_, event, app)
    if event == hs.application.watcher.activated then
        if app and app:bundleID() == egoBundleID then
            egoSpaceRename.hotkey:enable()
        else
            egoSpaceRename.hotkey:disable()
        end
    elseif event == hs.application.watcher.deactivated
        and app and app:bundleID() == egoBundleID then
        egoSpaceRename.hotkey:disable()
    end
end)
egoSpaceRename.watcher:start()
local activeApp = hs.application.frontmostApplication()
if activeApp and activeApp:bundleID() == egoBundleID then
    egoSpaceRename.hotkey:enable()
end

hs.alert.show("Hammerspoon 로드됨")
