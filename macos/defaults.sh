#!/bin/bash

# ===========================================
# macOS defaults configuration
# Run: ./macos/defaults.sh
# ===========================================

set -e

echo "Configuring macOS defaults..."

# Close System Preferences to prevent overriding
osascript -e 'tell application "System Preferences" to quit' 2>/dev/null || true
osascript -e 'tell application "System Settings" to quit' 2>/dev/null || true

# ===========================================
# Dock
# ===========================================
echo "  → Dock settings..."

# 자동 숨김
defaults write com.apple.dock autohide -bool true

# 아이콘 크기
defaults write com.apple.dock tilesize -int 52

# 마우스 오버시 확대
defaults write com.apple.dock magnification -bool true
defaults write com.apple.dock largesize -int 87

# Dock 위치 (bottom, left, right)
defaults write com.apple.dock orientation -string "bottom"

# 최소화 효과 (genie, scale)
defaults write com.apple.dock mineffect -string "genie"

# 최근 사용 앱 표시
defaults write com.apple.dock show-recents -bool true

# App Expose 제스처 활성화
defaults write com.apple.dock showAppExposeGestureEnabled -bool true

# ===========================================
# Hot Corners (Mission Control)
# 0: no-op, 2: Mission Control, 3: Application Windows,
# 4: Desktop, 5: Start Screen Saver, 6: Disable Screen Saver,
# 7: Dashboard, 10: Put Display to Sleep, 11: Launchpad,
# 12: Notification Center, 13: Lock Screen, 14: Quick Note
# ===========================================
echo "  → Hot Corners..."

# 우측 하단: Quick Note (14)
defaults write com.apple.dock wvous-br-corner -int 14
defaults write com.apple.dock wvous-br-modifier -int 0

# 나머지 코너 비활성화 (필요시 수정)
# defaults write com.apple.dock wvous-tl-corner -int 0
# defaults write com.apple.dock wvous-tr-corner -int 0
# defaults write com.apple.dock wvous-bl-corner -int 0

# ===========================================
# Finder
# ===========================================
echo "  → Finder settings..."

# 타이틀바에 전체 경로 표시
defaults write com.apple.finder _FXShowPosixPathInTitle -bool true

# 모든 파일 확장자 표시
defaults write NSGlobalDomain AppleShowAllExtensions -bool true

# 숨김 파일 표시 (필요시 활성화)
# defaults write com.apple.finder AppleShowAllFiles -bool true

# 경로바 표시
defaults write com.apple.finder ShowPathbar -bool true

# 상태바 표시
defaults write com.apple.finder ShowStatusBar -bool true

# 검색 시 현재 폴더 기본
defaults write com.apple.finder FXDefaultSearchScope -string "SCcf"

# 확장자 변경 경고 비활성화
defaults write com.apple.finder FXEnableExtensionChangeWarning -bool false

# .DS_Store 생성 안함 (네트워크/USB)
defaults write com.apple.desktopservices DSDontWriteNetworkStores -bool true
defaults write com.apple.desktopservices DSDontWriteUSBStores -bool true

# ===========================================
# Keyboard
# ===========================================
echo "  → Keyboard settings..."

# 키 반복 속도 (낮을수록 빠름, 기본값 6)
defaults write NSGlobalDomain KeyRepeat -int 2

# 키 반복 시작 딜레이 (낮을수록 빠름, 기본값 25)
defaults write NSGlobalDomain InitialKeyRepeat -int 15

# Press and hold 비활성화 (악센트 대신 키 반복)
defaults write NSGlobalDomain ApplePressAndHoldEnabled -bool false

# 자동 대문자 비활성화
defaults write NSGlobalDomain NSAutomaticCapitalizationEnabled -bool false

# 자동 마침표 (스페이스 두번) 활성화
defaults write NSGlobalDomain NSAutomaticPeriodSubstitutionEnabled -bool true

# 자동 따옴표 변환 활성화
defaults write NSGlobalDomain NSAutomaticQuoteSubstitutionEnabled -bool true

# 자동 대시 변환 활성화
defaults write NSGlobalDomain NSAutomaticDashSubstitutionEnabled -bool true

# 자동 맞춤법 교정 활성화
defaults write NSGlobalDomain NSAutomaticSpellingCorrectionEnabled -bool true

# 전체 키보드 접근 활성화 (탭으로 모든 컨트롤 이동)
defaults write NSGlobalDomain AppleKeyboardUIMode -int 2

# fn 키 기본 동작 (0: F1-F12, 1: 특수기능)
defaults write NSGlobalDomain com.apple.keyboard.fnState -bool false

# fn 키 동작 (0: 아무것도 안함, 1: 입력소스 전환, 2: 이모지, 3: 받아쓰기)
# Karabiner에서 fn키를 사용하므로 0으로 설정
defaults write com.apple.HIToolbox AppleFnUsageType -int 0

# Caps Lock으로 한영전환 (구름 입력기용, 효과 없을 수 있음)
defaults write com.apple.HIToolbox AppleCapsLockSwitchesInputMode -bool true
defaults write org.youknowone.inputmethod.Gureum CapsLockToToggleInputMode -bool true 2>/dev/null || true

# F18을 입력 소스 전환 단축키로 설정 (Karabiner에서 left_control → F18 매핑과 함께 사용)
# keycode 79 = F18, modifier 0 = 없음
defaults write com.apple.symbolichotkeys AppleSymbolicHotKeys -dict-add 60 '
<dict>
  <key>enabled</key><true/>
  <key>value</key>
  <dict>
    <key>type</key><string>standard</string>
    <key>parameters</key>
    <array>
      <integer>65535</integer>
      <integer>79</integer>
      <integer>0</integer>
    </array>
  </dict>
</dict>'

# ===========================================
# Trackpad
# ===========================================
echo "  → Trackpad settings..."

# 탭하여 클릭
defaults write com.apple.AppleMultitouchTrackpad Clicking -bool true
defaults write com.apple.driver.AppleBluetoothMultitouch.trackpad Clicking -bool true
defaults -currentHost write NSGlobalDomain com.apple.mouse.tapBehavior -int 1

# 세 손가락 드래그 활성화
defaults write com.apple.AppleMultitouchTrackpad TrackpadThreeFingerDrag -bool true
defaults write com.apple.driver.AppleBluetoothMultitouch.trackpad TrackpadThreeFingerDrag -bool true

# 두 손가락 우클릭
defaults write com.apple.AppleMultitouchTrackpad TrackpadRightClick -bool true

# 자연스러운 스크롤 방향
defaults write NSGlobalDomain com.apple.swipescrolldirection -bool true

# 트랙패드 속도 (0-3)
defaults write NSGlobalDomain com.apple.trackpad.scaling -float 3

# 스와이프로 페이지 이동
defaults write NSGlobalDomain AppleEnableSwipeNavigateWithScrolls -bool true

# ===========================================
# Mouse
# ===========================================
echo "  → Mouse settings..."

# 마우스 속도 (0-3)
defaults write NSGlobalDomain com.apple.mouse.scaling -float 2

# 스크롤휠 속도
defaults write NSGlobalDomain com.apple.scrollwheel.scaling -float 5

# 더블클릭 속도
defaults write NSGlobalDomain com.apple.mouse.doubleClickThreshold -float 0.8

# ===========================================
# Screenshot
# ===========================================
echo "  → Screenshot settings..."

# 스크린샷 저장 위치 (기본: ~/Desktop)
# mkdir -p ~/Screenshots
# defaults write com.apple.screencapture location -string "$HOME/Screenshots"

# 스크린샷 포맷 (png, jpg, gif, pdf, tiff)
defaults write com.apple.screencapture type -string "png"

# 그림자 비활성화
# defaults write com.apple.screencapture disable-shadow -bool true

# 스크린샷 이름 변경 (기본: "Screenshot")
# defaults write com.apple.screencapture name -string "Screenshot"

# 날짜 포맷 포함 여부
# defaults write com.apple.screencapture include-date -bool true

# ===========================================
# Menu Bar
# ===========================================
echo "  → Menu Bar settings..."

# 24시간제
defaults write com.apple.menuextra.clock Show24Hour -bool true

# 요일 표시
defaults write com.apple.menuextra.clock ShowDayOfWeek -bool true

# 날짜 표시
defaults write com.apple.menuextra.clock ShowDate -int 0

# 초 표시
defaults write com.apple.menuextra.clock ShowSeconds -bool false

# 배터리 퍼센트 표시
defaults write com.apple.menuextra.battery ShowPercent -string "YES"

# ===========================================
# System
# ===========================================
echo "  → System settings..."

# 다크 모드
defaults write NSGlobalDomain AppleInterfaceStyle -string "Dark"

# 24시간제 시스템 설정
defaults write NSGlobalDomain AppleICUForce24HourTime -bool true

# 스프링 로딩 딜레이 (Finder 폴더 호버)
defaults write NSGlobalDomain com.apple.springing.delay -float 0.6
defaults write NSGlobalDomain com.apple.springing.enabled -bool true

# 충돌 리포터 비활성화
defaults write com.apple.CrashReporter DialogType -string "none"

# ===========================================
# Safari (개인정보 보호)
# ===========================================
# echo "  → Safari settings..."

# 개발자 메뉴 활성화
# defaults write com.apple.Safari IncludeDevelopMenu -bool true
# defaults write com.apple.Safari WebKitDeveloperExtrasEnabledPreferenceKey -bool true

# Do Not Track 활성화
# defaults write com.apple.Safari SendDoNotTrackHTTPHeader -bool true

# ===========================================
# TextEdit
# ===========================================
echo "  → TextEdit settings..."

# 일반 텍스트 기본값
defaults write com.apple.TextEdit RichText -int 0

# 새 문서 인코딩 (UTF-8)
defaults write com.apple.TextEdit PlainTextEncoding -int 4
defaults write com.apple.TextEdit PlainTextEncodingForWrite -int 4

# ===========================================
# Activity Monitor
# ===========================================
echo "  → Activity Monitor settings..."

# Dock 아이콘 CPU 사용량 표시
defaults write com.apple.ActivityMonitor IconType -int 5

# 모든 프로세스 표시 (현재 macOS는 0을 100으로 바꿔 저장한다)
defaults write com.apple.ActivityMonitor ShowCategory -int 100

# ===========================================
# Apply Changes
# ===========================================
echo ""
echo "Applying changes..."

# Dock 재시작
killall Dock 2>/dev/null || true

# Finder 재시작
killall Finder 2>/dev/null || true

# SystemUIServer 재시작 (메뉴바)
killall SystemUIServer 2>/dev/null || true

echo ""
echo "✅ macOS defaults configured!"
echo "ℹ️  Some changes require logout/restart to take effect."
echo ""
