-- Sequential Clipboard Queue v3 for macOS Monterey 12+
-- Requires Hammerspoon.
--
-- Cmd+C remains completely normal. The clipboard watcher queues text.
-- Cmd+V pastes the next queued item.
-- Cmd+Shift+Option+R clears the queue.
-- Cmd+Shift+Option+S shows status.
-- Cmd+Shift+Option+P toggles sequential-paste interception.
--
-- Fix versus v2: the Cmd+V hotkey remains disabled until the synthetic paste
-- keystroke has completed. Re-enabling it immediately caused the generated
-- Cmd+V to be intercepted again and swallowed.

clipq3_items = clipq3_items or {}
clipq3_next = clipq3_next or 1
clipq3_capture_enabled = true
clipq3_paste_enabled = true
clipq3_paste_in_flight = false
clipq3_own_change_count = nil
clipq3_last_seen_change_count = hs.pasteboard.changeCount()

local function clipq3_remaining()
    return math.max(0, #clipq3_items - clipq3_next + 1)
end

local function clipq3_status_text()
    return "Captured: " .. tostring(#clipq3_items) ..
        "\nPasted/advanced: " .. tostring(clipq3_next - 1) ..
        "\nRemaining: " .. tostring(clipq3_remaining()) ..
        "\nCapture: " .. (clipq3_capture_enabled and "ON" or "OFF") ..
        "\nPaste interception: " .. (clipq3_paste_enabled and "ON" or "OFF") ..
        "\nPaste in flight: " .. (clipq3_paste_in_flight and "YES" or "NO")
end

local function clipq3_update_menu()
    if clipq3_status_item then
        clipq3_status_item:setTitle("Clip Queue: " .. tostring(clipq3_remaining()))
        clipq3_status_item:setTooltip(clipq3_status_text())
    end
end

local function clipq3_show_status()
    hs.alert.show(clipq3_status_text(), 4)
end

local function clipq3_clear()
    clipq3_items = {}
    clipq3_next = 1
    clipq3_paste_in_flight = false
    clipq3_own_change_count = nil
    clipq3_update_menu()
    hs.alert.show("Queue cleared", 1.5)
end

if clipq3_watcher then clipq3_watcher:stop() end
if clipq3_paste_hotkey then clipq3_paste_hotkey:delete() end
if clipq3_clear_hotkey then clipq3_clear_hotkey:delete() end
if clipq3_status_hotkey then clipq3_status_hotkey:delete() end
if clipq3_toggle_paste_hotkey then clipq3_toggle_paste_hotkey:delete() end
if clipq3_status_item then clipq3_status_item:delete() end

-- Poll every 0.05 sec. Polling may still miss very rapid consecutive copies.
hs.pasteboard.watcher.interval(0.05)

clipq3_watcher = hs.pasteboard.watcher.new(function(text)
    if not clipq3_capture_enabled then return end
    if type(text) ~= "string" or text == "" then return end

    local cc = hs.pasteboard.changeCount()
    if cc == clipq3_last_seen_change_count then return end
    clipq3_last_seen_change_count = cc

    if clipq3_own_change_count ~= nil and cc == clipq3_own_change_count then
        clipq3_own_change_count = nil
        return
    end
    clipq3_own_change_count = nil

    table.insert(clipq3_items, text)
    clipq3_update_menu()
end)

clipq3_paste_hotkey = hs.hotkey.bind({"cmd"}, "v", function()
    if not clipq3_paste_enabled then
        -- Temporarily disable this hotkey so Cmd+V passes through normally.
        clipq3_paste_hotkey:disable()
        hs.eventtap.keyStroke({"cmd"}, "v")
        hs.timer.doAfter(0.35, function()
            if clipq3_paste_hotkey then clipq3_paste_hotkey:enable() end
        end)
        return
    end

    if clipq3_paste_in_flight then return end

    if clipq3_next > #clipq3_items then
        hs.alert.show("Sequential queue is empty", 1)
        return
    end

    clipq3_paste_in_flight = true
    local text = clipq3_items[clipq3_next]
    clipq3_next = clipq3_next + 1

    hs.pasteboard.setContents(text)
    clipq3_own_change_count = hs.pasteboard.changeCount()
    clipq3_last_seen_change_count = clipq3_own_change_count
    clipq3_update_menu()

    -- Keep the hotkey disabled until after the generated Cmd+V is delivered.
    hs.timer.doAfter(0.08, function()
        clipq3_paste_hotkey:disable()
        hs.eventtap.keyStroke({"cmd"}, "v") -- default keydown/up delay
        hs.timer.doAfter(0.35, function()
            if clipq3_paste_hotkey then clipq3_paste_hotkey:enable() end
            clipq3_paste_in_flight = false
            clipq3_update_menu()
        end)
    end)
end)

clipq3_clear_hotkey = hs.hotkey.bind({"cmd", "shift", "alt"}, "r", clipq3_clear)
clipq3_status_hotkey = hs.hotkey.bind({"cmd", "shift", "alt"}, "s", clipq3_show_status)
clipq3_toggle_paste_hotkey = hs.hotkey.bind({"cmd", "shift", "alt"}, "p", function()
    clipq3_paste_enabled = not clipq3_paste_enabled
    clipq3_update_menu()
    hs.alert.show("Paste interception " .. (clipq3_paste_enabled and "ON" or "OFF"), 1.5)
end)

clipq3_status_item = hs.menubar.new()
clipq3_status_item:setMenu(function()
    return {
        { title = "Show queue status", fn = clipq3_show_status },
        { title = "Clear queue / start over", fn = clipq3_clear },
        { title = "-" },
        {
            title = clipq3_capture_enabled and "Pause capture" or "Resume capture",
            fn = function()
                clipq3_capture_enabled = not clipq3_capture_enabled
                clipq3_update_menu()
            end
        },
        {
            title = clipq3_paste_enabled and "Pause paste interception" or "Resume paste interception",
            fn = function()
                clipq3_paste_enabled = not clipq3_paste_enabled
                clipq3_update_menu()
            end
        },
        { title = "-" },
        { title = "Cmd+Shift+Option+S = status", disabled = true },
        { title = "Cmd+Shift+Option+R = clear", disabled = true },
        { title = "Cmd+Shift+Option+P = toggle paste interception", disabled = true },
    }
end)

clipq3_update_menu()
hs.alert.show("Sequential Clipboard Queue v3 loaded", 2)
